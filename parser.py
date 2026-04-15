from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import argparse
import sys
import time


def create_driver(headless: bool = False) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-popup-blocking")

    return webdriver.Chrome(service=Service(), options=options)


def open_omarket(driver: webdriver.Chrome, timeout: int = 20) -> None:
    url = "https://omarket.kz/"
    driver.get(url)

    wait = WebDriverWait(driver, timeout)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

    print("Opened page:", driver.title)
    print("URL:", driver.current_url)

    try:
        main_heading = driver.find_element(By.TAG_NAME, "h1").text
        print("Main heading:", main_heading)
    except Exception:
        print("Main heading not found or not available.")

    body_text = driver.find_element(By.TAG_NAME, "body").text
    print("Sample body text:", body_text.strip()[:240].replace("\n", " "))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Selenium parser for oMarket.kz")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--timeout", type=int, default=20, help="Timeout for page loading and waits")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    driver = None

    try:
        driver = create_driver(headless=args.headless)
        open_omarket(driver, timeout=args.timeout)
        return 0
    except Exception as error:
        print("Error:", error, file=sys.stderr)
        return 1
    finally:
        if driver:
            time.sleep(1)
            driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
