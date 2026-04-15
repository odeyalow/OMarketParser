from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, quote_plus, unquote, urlencode, urljoin, urlsplit

import requests
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait

BASE_URL = "https://omarket.kz/"
SEARCH_URL_PREFIX = "https://omarket.kz/catalog/search?query="
SEARCH_API_URL = "https://omarket.kz/catalog/api/search"
SEARCH_INPUT_SELECTOR = "input[maxlength='350']"
PRODUCT_CARD_SELECTOR = "a[href*='.html'][target='_blank']"
CURRENT_PAGE_SELECTOR = "button[data-type='page'][aria-current='page']"
NEXT_PAGE_SELECTOR = "button[aria-label='Next Page']"
ENSTRU_SHOW_SELECTOR = (
    "div.cursor-pointer.font-semibold.text-omarket-blue-800."
    "hover\\:underline.break-words"
)
ENSTRU_CHECKBOX_SELECTOR = "button#exclude_ooi[role='checkbox']"
ENSTRU_TABLE_SELECTOR = "div.relative.w-full.overflow-auto table"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)
SEARCH_RESULTS_PER_PAGE = 12


@dataclass(slots=True)
class ProductCard:
    product_url: str
    preview_text: str


@dataclass(slots=True)
class EnstruTableRow:
    query: str
    product_name: str
    product_url: str
    product_kztin: str | None
    enstru_code: str
    enstru_name: str


@dataclass(slots=True)
class CrawlResult:
    query: str
    search_url: str
    page_count: int
    product_count: int
    row_count: int
    rows: list[EnstruTableRow]


@dataclass(slots=True)
class OMarketSnapshot:
    title: str
    current_url: str
    submitted_query: str | None = None


class CrawlStopRequested(Exception):
    pass


def parse_positive_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        parsed = int(value)
    except (TypeError, ValueError):
        return None

    return parsed if parsed >= 0 else None


def build_search_url(query: str) -> str:
    return f"{SEARCH_URL_PREFIX}{quote_plus(query)}"


def build_product_api_url(product_url: str) -> str:
    parsed_url = urlsplit(product_url)
    if not parsed_url.path.startswith("/catalog/"):
        raise RuntimeError(f"Unsupported OMarket product URL: {product_url}")

    api_path = f"/catalog/api{parsed_url.path}"
    if parsed_url.query:
        api_path = f"{api_path}?{parsed_url.query}"

    return urljoin(BASE_URL, api_path)


def base36_encode(value: int) -> str:
    if value == 0:
        return "0"

    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    encoded = ""
    remainder = value

    while remainder:
        remainder, digit = divmod(remainder, 36)
        encoded = f"{alphabet[digit]}{encoded}"

    return encoded


def omarket_signature_seed(value: str) -> str:
    char_code_sum = sum(ord(char) for char in hashlib.sha256(value.encode("utf-8")).hexdigest())
    return re.sub(r"(0+|\.)", "", base36_encode(char_code_sum * 30))


def encode_signature_query_value(value: Any) -> str:
    return quote(str(value), safe="")


def build_actual_query_string(params: list[tuple[str, Any]]) -> str:
    clean_params: list[tuple[str, str]] = []

    for key, value in params:
        if value is None:
            continue

        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue

        clean_params.append((key, str(value)))

    return urlencode(clean_params, doseq=True)


def build_signature_query_string(params: list[tuple[str, Any]]) -> str:
    clean_pairs: list[str] = []

    for key, value in params:
        if value is None:
            continue

        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue

        clean_pairs.append(f"{quote(str(key), safe='')}={encode_signature_query_value(value)}")

    return "&".join(clean_pairs)


def build_product_preview_text(name: str, kztin: str | None) -> str:
    preview_parts = [normalize_space(name)]
    if kztin:
        preview_parts.append(f"KZTIN: {kztin}")
    return " | ".join(part for part in preview_parts if part)


def compose_enstru_name(entry: dict[str, Any]) -> str:
    base_name = normalize_space(entry.get("name_ru") or entry.get("name_kz"))
    description = normalize_space(entry.get("desc_ru") or entry.get("desc_kz"))

    if base_name and description:
        return f"{base_name} ({description})"
    return base_name or description


class OMarketApiClient:
    def __init__(
        self,
        *,
        timeout: int = 30,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.timeout = timeout
        self._csrf_token: str | None = None
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            }
        )

    def close(self) -> None:
        self._session.close()

    def bootstrap(self) -> None:
        if self._csrf_token:
            return

        response = self._session.get(
            BASE_URL,
            timeout=self.timeout,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        response.raise_for_status()

        csrf_match = re.search(
            r'<meta name="csrf-token" content="([^"]+)"',
            response.text,
        )
        if not csrf_match:
            raise RuntimeError("Unable to initialize OMarket session.")

        self._csrf_token = csrf_match.group(1)

    def fetch_html(self, url: str) -> str:
        self.bootstrap()
        response = self._session.get(
            url,
            timeout=self.timeout,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        response.raise_for_status()
        return response.text

    def search_products(
        self,
        query: str,
        *,
        page: int,
        per_page: int = SEARCH_RESULTS_PER_PAGE,
    ) -> dict[str, Any]:
        params = [
            ("query", query),
            ("byProperties", "false"),
            ("bySubject", "false"),
            ("isOtp", "false"),
            ("isDistributor", "false"),
            ("isOoi", "false"),
            ("withoutSign", "false"),
            ("perPage", per_page),
            ("page", page),
            ("disableCorrecting", "false"),
        ]
        payload = self._request_json(
            SEARCH_API_URL,
            params=params,
            referer=BASE_URL,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected search response from OMarket.")
        return payload

    def fetch_product_rows(
        self,
        query: str,
        product_url: str,
    ) -> list[EnstruTableRow]:
        payload = self._request_json(
            build_product_api_url(product_url),
            referer=product_url,
        )
        product_data = payload.get("data")
        if not isinstance(product_data, dict):
            raise RuntimeError("Unexpected product response from OMarket.")

        product_name = normalize_space(product_data.get("name")) or "Untitled product"
        product_kztin = normalize_space(product_data.get("kztin")) or None
        enstru_entries = product_data.get("enstru_list") or product_data.get("product_enstru_list") or []

        if not isinstance(enstru_entries, list):
            raise RuntimeError("Unexpected ENSTRU data in OMarket response.")

        rows: list[EnstruTableRow] = []
        for entry in enstru_entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("ooi"):
                continue

            enstru_code = normalize_space(entry.get("enstru"))
            enstru_name = compose_enstru_name(entry)
            if not enstru_code or not enstru_name:
                continue

            rows.append(
                EnstruTableRow(
                    query=query,
                    product_name=product_name,
                    product_url=product_url,
                    product_kztin=product_kztin,
                    enstru_code=enstru_code,
                    enstru_name=enstru_name,
                )
            )

        return deduplicate_enstru_rows(rows)

    def _request_json(
        self,
        url: str,
        *,
        params: list[tuple[str, Any]] | None = None,
        referer: str,
    ) -> dict[str, Any]:
        self.bootstrap()
        params = params or []

        actual_query = build_actual_query_string(params)
        signature_query = build_signature_query_string(params)

        parsed_url = urlsplit(url)
        request_path = parsed_url.path
        existing_actual_query = parsed_url.query
        actual_url = url

        if actual_query:
            actual_url = (
                f"{url}&{actual_query}" if existing_actual_query else f"{url}?{actual_query}"
            )
        if signature_query:
            merged_signature_query = (
                f"{parsed_url.query}&{signature_query}"
                if parsed_url.query
                else signature_query
            )
            request_path = unquote(f"{request_path}?{merged_signature_query}")
        elif parsed_url.query:
            request_path = unquote(f"{request_path}?{parsed_url.query}")

        signature_base = f"GET {request_path}"
        header_name = omarket_signature_seed(signature_base)
        header_value = omarket_signature_seed(
            f"{signature_base} {hashlib.sha256(b'').hexdigest()}"
        )

        response = self._session.get(
            actual_url,
            timeout=self.timeout,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": referer,
                "X-CSRF-TOKEN": self._csrf_token or "",
                "X-Requested-With": "XMLHttpRequest",
                header_name: header_value,
            },
        )

        if response.ok:
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("Unexpected JSON payload from OMarket.")
            return payload

        error_message = None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                error_message = normalize_space(payload.get("message"))
        except Exception:
            error_message = None

        detail = error_message or normalize_space(response.text) or response.reason
        raise RuntimeError(
            f"OMarket API request failed with HTTP {response.status_code}: {detail}"
        )


def collect_product_cards_from_search_payload(payload: dict[str, Any]) -> list[ProductCard]:
    cards: list[ProductCard] = []
    seen_urls: set[str] = set()

    raw_results = payload.get("searchResults") or []
    if not isinstance(raw_results, list):
        return cards

    for raw_item in raw_results:
        if not isinstance(raw_item, dict):
            continue

        link = normalize_space(raw_item.get("link"))
        if not link:
            continue

        product_url = urljoin(BASE_URL, link)
        if product_url in seen_urls:
            continue

        preview_text = build_product_preview_text(
            normalize_space(raw_item.get("name") or raw_item.get("name_output")),
            normalize_space(raw_item.get("kztin")) or None,
        )

        seen_urls.add(product_url)
        cards.append(
            ProductCard(
                product_url=product_url,
                preview_text=preview_text or product_url,
            )
        )

    return cards


def build_driver(headless: bool) -> WebDriver:
    options = ChromeOptions()
    if headless:
        options.add_argument("--headless=new")

    for argument in (
        "--window-size=1920,1080",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        f"--user-agent={DEFAULT_USER_AGENT}",
    ):
        options.add_argument(argument)

    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en-US', 'en']});
                Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
            """
        },
    )
    return driver


def normalize_space(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def wait_until_document_is_ready(driver: WebDriver, timeout: int) -> None:
    WebDriverWait(driver, timeout).until(
        lambda current_driver: current_driver.execute_script("return document.readyState")
        == "complete"
    )


def wait_for_truthy(
    producer: Callable[[], Any],
    timeout: int,
    error_message: str,
    interval: float = 0.25,
    stop_checker: Callable[[], bool] | None = None,
) -> Any:
    deadline = time.time() + timeout

    while time.time() < deadline:
        if stop_checker and stop_checker():
            raise CrawlStopRequested()

        try:
            value = producer()
            if value:
                return value
        except (StaleElementReferenceException, NoSuchElementException):
            pass

        time.sleep(interval)

    raise RuntimeError(error_message)


def click_element(driver: WebDriver, element: WebElement) -> None:
    try:
        element.click()
    except Exception:
        driver.execute_script("arguments[0].click();", element)


def find_search_input(driver: WebDriver, timeout: int) -> WebElement:
    def producer() -> WebElement | None:
        for element in driver.find_elements(By.CSS_SELECTOR, SEARCH_INPUT_SELECTOR):
            if element.is_displayed() and element.is_enabled():
                return element
        return None

    return wait_for_truthy(
        producer,
        timeout=timeout,
        error_message="Unable to find the OMarket search input.",
    )


def submit_search_query(driver: WebDriver, query: str, timeout: int) -> None:
    search_input = find_search_input(driver, timeout=timeout)
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", search_input)
    click_element(driver, search_input)
    search_input.send_keys(Keys.CONTROL, "a")
    search_input.send_keys(Keys.DELETE)
    search_input.send_keys(query)
    search_input.send_keys(Keys.ENTER)

    expected_fragment = quote_plus(query)
    WebDriverWait(driver, timeout).until(
        lambda current_driver: (
            SEARCH_URL_PREFIX in current_driver.current_url
            and expected_fragment in current_driver.current_url
        )
    )
    wait_until_document_is_ready(driver, timeout=timeout)


def collect_product_cards(driver: WebDriver) -> list[ProductCard]:
    cards: list[ProductCard] = []
    seen_urls: set[str] = set()

    for anchor in driver.find_elements(By.CSS_SELECTOR, PRODUCT_CARD_SELECTOR):
        href = normalize_space(anchor.get_attribute("href"))
        preview_text = normalize_space(anchor.text)

        if not href or href in seen_urls:
            continue
        if not href.startswith("https://omarket.kz/catalog/"):
            continue
        if "KZTIN:" not in preview_text:
            continue

        seen_urls.add(href)
        cards.append(ProductCard(product_url=href, preview_text=preview_text))

    return cards


def get_current_results_page(
    driver: WebDriver,
    *,
    default: int | None = None,
) -> int:
    try:
        page_button = wait_for_truthy(
            lambda: driver.find_element(By.CSS_SELECTOR, CURRENT_PAGE_SELECTOR),
            timeout=10,
            error_message="Unable to determine the current search results page.",
        )
    except RuntimeError:
        if default is not None:
            return default
        raise
    page_value = normalize_space(
        page_button.get_attribute("value")
        or page_button.get_attribute("aria-label")
        or page_button.text
    )
    match = re.search(r"\d+", page_value)
    if not match:
        raise RuntimeError("Unable to parse the current search results page number.")
    return int(match.group())


def go_to_next_results_page(
    driver: WebDriver,
    timeout: int,
    stop_checker: Callable[[], bool] | None = None,
) -> bool:
    current_page = get_current_results_page(driver, default=1)

    def producer() -> WebElement | None:
        for button in driver.find_elements(By.CSS_SELECTOR, NEXT_PAGE_SELECTOR):
            if button.is_displayed() and button.get_attribute("disabled") is None:
                return button
        return None

    next_button = producer()
    if next_button is None:
        return False

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_button)
    click_element(driver, next_button)

    wait_for_truthy(
        lambda: get_current_results_page(driver) == current_page + 1,
        timeout=timeout,
        error_message="OMarket did not switch to the next search results page in time.",
        stop_checker=stop_checker,
    )
    wait_for_product_cards(driver, timeout=timeout, stop_checker=stop_checker)
    time.sleep(0.5)
    return True


def _wait_for_product_cards_legacy(
    driver: WebDriver,
    timeout: int,
    stop_checker: Callable[[], bool] | None = None,
) -> list[ProductCard]:
    def producer() -> list[ProductCard]:
        cards = collect_product_cards(driver)
        if cards:
            return cards

        body_text = normalize_space(driver.find_element(By.TAG_NAME, "body").text).lower()
        if "ничего не найдено" in body_text:
            return []

        return []

    deadline = time.time() + timeout
    while time.time() < deadline:
        if stop_checker and stop_checker():
            raise CrawlStopRequested()

        cards = producer()
        if cards:
            return cards
        time.sleep(0.5)

    raise RuntimeError("Search result cards did not load in time.")


def wait_for_product_cards(
    driver: WebDriver,
    timeout: int,
    stop_checker: Callable[[], bool] | None = None,
) -> list[ProductCard]:
    no_results_marker = object()

    def producer() -> list[ProductCard] | object | None:
        cards = collect_product_cards(driver)
        if cards:
            return cards

        body_text = normalize_space(driver.find_element(By.TAG_NAME, "body").text).lower()
        if "ничего не найдено" in body_text or "рѕрёс‡рµрірѕ" in body_text:
            return no_results_marker

        return None

    deadline = time.time() + timeout
    while time.time() < deadline:
        if stop_checker and stop_checker():
            raise CrawlStopRequested()

        result = producer()
        if result is no_results_marker:
            return []
        if result:
            return result
        time.sleep(0.5)

    raise RuntimeError("Search result cards did not load in time.")


def extract_product_name(driver: WebDriver) -> str:
    title = normalize_space(driver.title)
    for separator in (
        " купить на Omarket.kz",
        " - ОМаркет - Omarket.kz",
        " — Omarket.kz",
    ):
        if separator in title:
            return normalize_space(title.split(separator, 1)[0])
    return title or "Untitled product"


def extract_product_kztin(driver: WebDriver) -> str | None:
    body_text = driver.find_element(By.TAG_NAME, "body").text
    match = re.search(r"KZTIN\s+([0-9]{6,})", body_text)
    return match.group(1) if match else None


def get_enstru_table(driver: WebDriver, timeout: int) -> WebElement:
    return wait_for_truthy(
        lambda: driver.find_element(By.CSS_SELECTOR, ENSTRU_TABLE_SELECTOR),
        timeout=timeout,
        error_message="Unable to find the ENSTRU table.",
    )


def collect_enstru_row_pairs(table: WebElement) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []

    for row in table.find_elements(By.CSS_SELECTOR, "tbody tr"):
        cells = row.find_elements(By.TAG_NAME, "td")
        if len(cells) < 2:
            continue

        enstru_code = normalize_space(cells[0].text)
        enstru_name = normalize_space(cells[1].text)
        if not enstru_code or not enstru_name:
            continue

        pairs.append((enstru_code, enstru_name))

    return pairs


def wait_for_enstru_row_pairs(
    driver: WebDriver,
    timeout: int,
    previous_pairs: list[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    deadline = time.time() + timeout
    stable_samples = 0
    last_pairs: list[tuple[str, str]] | None = None
    observed_change = previous_pairs is None
    poll_start = time.time()

    while time.time() < deadline:
        try:
            tables = driver.find_elements(By.CSS_SELECTOR, ENSTRU_TABLE_SELECTOR)
            if not tables:
                time.sleep(0.25)
                continue
            pairs = collect_enstru_row_pairs(tables[0])
        except (NoSuchElementException, StaleElementReferenceException):
            time.sleep(0.25)
            continue

        if previous_pairs is not None and pairs != previous_pairs:
            observed_change = True

        if pairs == last_pairs:
            stable_samples += 1
        else:
            last_pairs = pairs
            stable_samples = 1

        if stable_samples >= 2 and (observed_change or time.time() - poll_start >= 1):
            return pairs

        time.sleep(0.25)

    raise RuntimeError("ENSTRU table rows did not stabilize in time.")


def deduplicate_enstru_rows(rows: list[EnstruTableRow]) -> list[EnstruTableRow]:
    unique_rows: list[EnstruTableRow] = []
    seen_keys: set[tuple[str, str, str]] = set()

    for row in rows:
        row_key = (row.product_url, row.enstru_code, row.enstru_name)
        if row_key in seen_keys:
            continue
        seen_keys.add(row_key)
        unique_rows.append(row)

    return unique_rows


def open_enstru_modal(driver: WebDriver, timeout: int) -> None:
    show_button = wait_for_truthy(
        lambda: next(
            (
                element
                for element in driver.find_elements(By.CSS_SELECTOR, ENSTRU_SHOW_SELECTOR)
                if normalize_space(element.text) == "Показать"
            ),
            None,
        ),
        timeout=timeout,
        error_message="Unable to find the ENSTRU 'Показать' button.",
    )
    click_element(driver, show_button)

    wait_for_truthy(
        lambda: driver.find_elements(By.CSS_SELECTOR, ENSTRU_CHECKBOX_SELECTOR)
        and driver.find_elements(By.CSS_SELECTOR, ENSTRU_TABLE_SELECTOR),
        timeout=timeout,
        error_message="ENSTRU modal did not open in time.",
    )
    wait_for_enstru_row_pairs(driver, timeout=timeout)


def enable_exclude_ooi(
    driver: WebDriver,
    timeout: int,
    previous_pairs: list[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    checkbox = wait_for_truthy(
        lambda: driver.find_element(By.CSS_SELECTOR, ENSTRU_CHECKBOX_SELECTOR),
        timeout=timeout,
        error_message="Unable to find the ENSTRU checkbox.",
    )

    used_previous_pairs = previous_pairs
    if checkbox.get_attribute("aria-checked") != "true":
        click_element(driver, checkbox)
        wait_for_truthy(
            lambda: checkbox.get_attribute("aria-checked") == "true"
            or checkbox.get_attribute("data-state") == "checked",
            timeout=timeout,
            error_message="ENSTRU checkbox did not switch to checked state.",
        )
    else:
        used_previous_pairs = None

    return wait_for_enstru_row_pairs(
        driver,
        timeout=timeout,
        previous_pairs=used_previous_pairs,
    )


def extract_enstru_rows(
    driver: WebDriver,
    query: str,
    product_name: str,
    product_url: str,
    product_kztin: str | None,
    timeout: int,
    row_pairs: list[tuple[str, str]] | None = None,
) -> list[EnstruTableRow]:
    pairs = row_pairs if row_pairs is not None else wait_for_enstru_row_pairs(driver, timeout=timeout)
    rows: list[EnstruTableRow] = []
    for enstru_code, enstru_name in pairs:
        rows.append(
            EnstruTableRow(
                query=query,
                product_name=product_name,
                product_url=product_url,
                product_kztin=product_kztin,
                enstru_code=enstru_code,
                enstru_name=enstru_name,
            )
        )

    return deduplicate_enstru_rows(rows)


def scrape_product_enstru_rows(
    driver: WebDriver,
    query: str,
    product_url: str,
    timeout: int,
) -> list[EnstruTableRow]:
    search_handle = driver.current_window_handle
    existing_handles = set(driver.window_handles)
    driver.execute_script("window.open(arguments[0], '_blank', 'noopener');", product_url)

    product_handle = wait_for_truthy(
        lambda: next(
            (handle for handle in driver.window_handles if handle not in existing_handles),
            None,
        ),
        timeout=timeout,
        error_message="Unable to open the product page in a new browser tab.",
    )

    driver.switch_to.window(product_handle)

    try:
        wait_until_document_is_ready(driver, timeout=timeout)
        time.sleep(1.5)

        product_name = extract_product_name(driver)
        product_kztin = extract_product_kztin(driver)

        open_enstru_modal(driver, timeout=timeout)
        previous_pairs = wait_for_enstru_row_pairs(driver, timeout=timeout)
        filtered_pairs = enable_exclude_ooi(
            driver,
            timeout=timeout,
            previous_pairs=previous_pairs,
        )

        return extract_enstru_rows(
            driver,
            query=query,
            product_name=product_name,
            product_url=product_url,
            product_kztin=product_kztin,
            timeout=timeout,
            row_pairs=filtered_pairs,
        )
    finally:
        driver.close()
        driver.switch_to.window(search_handle)


def crawl_search_results(
    query: str,
    *,
    headless: bool = False,
    timeout: int = 30,
    max_products: int | None = None,
    max_pages: int | None = None,
    stop_checker: Callable[[], bool] | None = None,
    on_driver_ready: Callable[[WebDriver | None], None] | None = None,
    on_search_ready: Callable[[str, int], None] | None = None,
    on_page_ready: Callable[[int, int, int, int | None], None] | None = None,
    on_product_start: Callable[[int, int, ProductCard], None] | None = None,
    on_product_finish: Callable[[int, int, ProductCard, list[EnstruTableRow]], None] | None = None,
    on_product_error: Callable[[int, int, ProductCard, str], None] | None = None,
) -> CrawlResult:
    normalized_query = normalize_space(query)
    if not normalized_query:
        raise ValueError("Search query is required.")
    if max_products is not None and max_products < 1:
        raise ValueError("max_products must be greater than zero.")
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be greater than zero.")
    search_url = build_search_url(normalized_query)
    rows: list[EnstruTableRow] = []
    seen_product_urls: set[str] = set()
    processed_products = 0
    discovered_products = 0
    visited_pages = 0
    has_search_callback_run = False
    total_results_count = 0

    if on_driver_ready:
        on_driver_ready(None)

    client = OMarketApiClient(timeout=timeout)
    try:
        client.bootstrap()

        while True:
            if stop_checker and stop_checker():
                raise CrawlStopRequested()

            page_number = visited_pages + 1
            payload = client.search_products(
                normalized_query,
                page=page_number,
                per_page=SEARCH_RESULTS_PER_PAGE,
            )

            total_results_count = (
                parse_positive_int(payload.get("totalResultsCount")) or total_results_count
            )
            raw_page_cards = collect_product_cards_from_search_payload(payload)
            page_cards: list[ProductCard] = []

            for product_card in raw_page_cards:
                if product_card.product_url in seen_product_urls:
                    continue
                if max_products is not None and discovered_products >= max_products:
                    break
                seen_product_urls.add(product_card.product_url)
                page_cards.append(product_card)
                discovered_products += 1

            visited_pages = page_number
            effective_total_products = total_results_count or discovered_products

            if not has_search_callback_run and on_search_ready:
                on_search_ready(search_url, effective_total_products)
                has_search_callback_run = True

            if on_page_ready:
                on_page_ready(
                    page_number,
                    effective_total_products,
                    len(page_cards),
                    max_pages,
                )

            for product_card in page_cards:
                if stop_checker and stop_checker():
                    raise CrawlStopRequested()

                processed_products += 1

                if on_product_start:
                    on_product_start(
                        processed_products,
                        effective_total_products,
                        product_card,
                    )

                try:
                    parsed_rows = client.fetch_product_rows(
                        normalized_query,
                        product_card.product_url,
                    )
                    rows.extend(parsed_rows)
                    if on_product_finish:
                        on_product_finish(
                            processed_products,
                            effective_total_products,
                            product_card,
                            parsed_rows,
                        )
                except Exception as error:
                    if on_product_error:
                        on_product_error(
                            processed_products,
                            effective_total_products,
                            product_card,
                            str(error),
                        )

            if max_products is not None and discovered_products >= max_products:
                break
            if max_pages is not None and visited_pages >= max_pages:
                break
            if stop_checker and stop_checker():
                raise CrawlStopRequested()
            if not raw_page_cards:
                break
            if total_results_count and visited_pages * SEARCH_RESULTS_PER_PAGE >= total_results_count:
                break
            if len(raw_page_cards) < SEARCH_RESULTS_PER_PAGE:
                break

        unique_rows = deduplicate_enstru_rows(rows)

        return CrawlResult(
            query=normalized_query,
            search_url=search_url,
            page_count=visited_pages,
            product_count=discovered_products,
            row_count=len(unique_rows),
            rows=unique_rows,
        )
    finally:
        if on_driver_ready:
            on_driver_ready(None)
        client.close()


def parse_omarket_homepage(
    headless: bool = False,
    timeout: int = 30,
    search_query: str | None = None,
) -> OMarketSnapshot:
    del headless

    client = OMarketApiClient(timeout=timeout)
    normalized_query = normalize_space(search_query)
    target_url = build_search_url(normalized_query) if normalized_query else BASE_URL

    try:
        html = client.fetch_html(target_url)
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        title = normalize_space(
            html_lib.unescape(title_match.group(1)) if title_match else "OMarket.kz"
        )
        return OMarketSnapshot(
            title=title or "OMarket.kz",
            current_url=target_url,
            submitted_query=normalized_query or None,
        )
    finally:
        client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Selenium crawler for OMarket.kz search results."
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chrome in headless mode.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Page load timeout in seconds.",
    )
    parser.add_argument(
        "--query",
        help="Search query to type into the OMarket search field.",
    )
    parser.add_argument(
        "--max-products",
        type=int,
        help="Optional limit for how many product pages to crawl.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        help="Optional limit for how many search results pages to traverse.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to a JSON file.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.query:
        payload = asdict(
            crawl_search_results(
                args.query,
                headless=args.headless,
                timeout=args.timeout,
                max_products=args.max_products,
                max_pages=args.max_pages,
            )
        )
    else:
        payload = asdict(
            parse_omarket_homepage(
                headless=args.headless,
                timeout=args.timeout,
            )
        )

    output = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.output:
        args.output.write_text(output, encoding="utf-8")

    print(output)


if __name__ == "__main__":
    main()
