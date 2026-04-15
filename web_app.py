from __future__ import annotations

import atexit
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from io import BytesIO
from threading import Lock, Thread
from typing import Any

from flask import Flask, jsonify, render_template, request, send_file
from openpyxl import Workbook
from openpyxl.styles import Font
from selenium.webdriver.remote.webdriver import WebDriver

from omarket_parser import (
    CrawlStopRequested,
    EnstruTableRow,
    ProductCard,
    crawl_search_results,
    normalize_space,
)

app = Flask(__name__)


def build_export_filename(query: str) -> str:
    normalized_query = normalize_space(query)
    safe_query = re.sub(r"[^A-Za-z0-9_-]+", "-", normalized_query).strip("-")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    prefix = safe_query[:40] or "omarket-results"
    return f"{prefix}-{timestamp}.xlsx"


def build_export_workbook(rows: list[dict[str, Any]]) -> BytesIO:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "ENSTRU"

    headers = [
        "Запрос",
        "Товар",
        "KZTIN",
        "URL товара",
        "ENSTRU",
        "Наименование",
    ]
    sheet.append(headers)

    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for row in rows:
        sheet.append(
            [
                row.get("query") or "",
                row.get("product_name") or "",
                row.get("product_kztin") or "",
                row.get("product_url") or "",
                row.get("enstru_code") or "",
                row.get("enstru_name") or "",
            ]
        )

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    column_widths = {
        "A": 22,
        "B": 42,
        "C": 18,
        "D": 70,
        "E": 20,
        "F": 44,
    }
    for column_name, width in column_widths.items():
        sheet.column_dimensions[column_name].width = width

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


@dataclass
class ParserState:
    status: str = "idle"
    query: str = ""
    max_pages: int | None = None
    message: str = "Готово к запуску."
    current_page: int = 0
    visited_pages: int = 0
    total_products: int = 0
    processed_products: int = 0
    current_product_url: str | None = None
    current_product_preview: str | None = None
    search_url: str | None = None
    stop_requested: bool = False
    row_count: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


class ParserController:
    def __init__(self) -> None:
        self._lock = Lock()
        self._thread: Thread | None = None
        self._active_driver: WebDriver | None = None
        self._stop_requested = False
        self._state = ParserState()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return asdict(self._state)

    def start(self, query: str, max_pages: int | None) -> dict[str, Any]:
        normalized_query = normalize_space(query)
        if not normalized_query:
            raise ValueError("Нужно указать поисковый запрос.")
        if max_pages is not None and max_pages < 1:
            raise ValueError("Количество страниц должно быть больше нуля.")

        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("Парсер уже запущен.")

            self._stop_requested = False
            self._state = ParserState(
                status="running",
                query=normalized_query,
                max_pages=max_pages,
                message="Подготовка быстрой сессии OMarket...",
            )
            self._thread = Thread(
                target=self._worker,
                args=(normalized_query, max_pages),
                daemon=True,
            )
            self._thread.start()
            return asdict(self._state)

    def reset(self) -> dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("Нельзя сбросить данные, пока парсер работает.")

            self._stop_requested = False
            self._state = ParserState(
                status="idle",
                message="История и таблица очищены. Готово к запуску.",
            )
            return asdict(self._state)

    def request_stop(self) -> dict[str, Any]:
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                raise RuntimeError("Парсер не запущен.")

            self._stop_requested = True
            self._state.stop_requested = True
            self._state.status = "stopping"
            if self._state.current_product_url:
                self._state.message = "Завершаю текущий товар перед остановкой..."
            else:
                self._state.message = "Остановка..."
            return asdict(self._state)

    def should_stop(self) -> bool:
        with self._lock:
            return self._stop_requested

    def close_active_driver(self) -> None:
        with self._lock:
            driver = self._active_driver
            self._active_driver = None

        if driver is None:
            return

        try:
            driver.quit()
        except Exception:
            pass

    def _set_active_driver(self, driver: WebDriver | None) -> None:
        with self._lock:
            self._active_driver = driver

    def _handle_search_ready(self, search_url: str, total_products: int) -> None:
        with self._lock:
            self._state.search_url = search_url
            self._state.total_products = total_products
            if total_products:
                self._state.message = f"Найдено карточек товаров: {total_products}."
            else:
                self._state.message = "По этому запросу карточки товаров не найдены."

    def _handle_page_ready(
        self,
        page_number: int,
        total_products: int,
        page_product_count: int,
        max_pages: int | None,
    ) -> None:
        with self._lock:
            self._state.current_page = page_number
            self._state.visited_pages = max(self._state.visited_pages, page_number)
            self._state.total_products = total_products
            self._state.max_pages = max_pages
            if self._state.current_product_url:
                return
            if page_product_count:
                self._state.message = (
                    f"Загружена страница {page_number}. "
                    f"На этой странице найдено товаров: {page_product_count}."
                )
            else:
                self._state.message = (
                    f"Загружена страница {page_number}. Новых товаров не найдено."
                )

    def _handle_product_start(
        self,
        index: int,
        total_products: int,
        product_card: ProductCard,
    ) -> None:
        with self._lock:
            self._state.current_product_url = product_card.product_url
            self._state.current_product_preview = product_card.preview_text
            if self._stop_requested:
                self._state.status = "stopping"
                self._state.message = (
                    f"Завершаю товар {index} из {total_products} перед остановкой..."
                )
            else:
                self._state.status = "running"
                self._state.message = f"Обработка товара {index} из {total_products}..."

    def _handle_product_finish(
        self,
        index: int,
        total_products: int,
        product_card: ProductCard,
        parsed_rows: list[EnstruTableRow],
    ) -> None:
        serialized_rows = [asdict(row) for row in parsed_rows]

        with self._lock:
            self._state.processed_products = index
            self._state.current_product_url = None
            self._state.current_product_preview = None
            self._state.results.extend(serialized_rows)
            self._state.row_count = len(self._state.results)
            if self._stop_requested:
                self._state.status = "stopping"
                self._state.message = "Текущий товар обработан. Выполняю остановку..."
            else:
                self._state.message = (
                    f"Товар {index} из {total_products} обработан. "
                    f"Сохранено строк: {len(parsed_rows)}."
                )

    def _handle_product_error(
        self,
        index: int,
        total_products: int,
        product_card: ProductCard,
        error_message: str,
    ) -> None:
        with self._lock:
            self._state.processed_products = index
            self._state.current_product_url = None
            self._state.current_product_preview = None
            self._state.error = error_message
            if self._stop_requested:
                self._state.status = "stopping"
                self._state.message = (
                    "Текущий товар завершился с ошибкой. Останавливаюсь после него..."
                )
            else:
                self._state.message = (
                    f"Товар {index} из {total_products} пропущен: {error_message}"
                )

    def _worker(self, query: str, max_pages: int | None) -> None:
        try:
            result = crawl_search_results(
                query,
                headless=True,
                timeout=30,
                max_pages=max_pages,
                stop_checker=self.should_stop,
                on_driver_ready=self._set_active_driver,
                on_search_ready=self._handle_search_ready,
                on_page_ready=self._handle_page_ready,
                on_product_start=self._handle_product_start,
                on_product_finish=self._handle_product_finish,
                on_product_error=self._handle_product_error,
            )

            with self._lock:
                self._state.search_url = result.search_url
                self._state.max_pages = max_pages
                self._state.current_page = result.page_count
                self._state.visited_pages = result.page_count
                self._state.total_products = result.product_count
                self._state.row_count = len(self._state.results)
                self._state.current_product_url = None
                self._state.current_product_preview = None

                if self._stop_requested:
                    self._state.status = "stopped"
                    self._state.message = (
                        "Парсер остановлен после завершения текущего товара."
                    )
                elif result.product_count == 0:
                    self._state.status = "completed"
                    self._state.message = (
                        "Парсинг завершен. Карточки товаров не найдены."
                    )
                elif self._state.row_count == 0:
                    self._state.status = "completed"
                    self._state.message = (
                        "Парсинг завершен. Строки таблицы не сохранены."
                    )
                else:
                    self._state.status = "completed"
                    self._state.message = (
                        f"Парсинг завершен. Сохранено строк таблицы: {self._state.row_count}."
                    )
        except CrawlStopRequested:
            with self._lock:
                self._state.status = "stopped"
                self._state.current_product_url = None
                self._state.current_product_preview = None
                if self._state.row_count:
                    self._state.message = (
                        "Парсер остановлен. Частичные результаты сохранены."
                    )
                else:
                    self._state.message = "Парсер остановлен."
        except Exception as error:  # pragma: no cover - defensive API boundary
            with self._lock:
                self._state.status = "error"
                self._state.current_product_url = None
                self._state.current_product_preview = None
                self._state.error = str(error)
                self._state.message = str(error)
        finally:
            self.close_active_driver()
            with self._lock:
                self._thread = None
                self._stop_requested = False
                self._state.stop_requested = False


controller = ParserController()


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/status")
def status():
    return jsonify({"ok": True, "data": controller.snapshot()})


@app.post("/api/run")
@app.post("/api/parse")
def run_parser():
    payload = request.get_json(silent=True) or {}
    query = str(payload.get("query") or "")
    raw_max_pages = payload.get("max_pages")

    if raw_max_pages in (None, "", "all"):
        max_pages = None
    else:
        try:
            max_pages = int(raw_max_pages)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Количество страниц должно быть числом."}), 400

    try:
        state = controller.start(query, max_pages)
        return jsonify({"ok": True, "data": state})
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except RuntimeError as error:
        return jsonify({"ok": False, "error": str(error)}), 409


@app.post("/api/stop")
def stop_parser():
    try:
        state = controller.request_stop()
        return jsonify({"ok": True, "data": state})
    except RuntimeError as error:
        return jsonify({"ok": False, "error": str(error)}), 409


@app.post("/api/reset")
def reset_parser():
    try:
        state = controller.reset()
        return jsonify({"ok": True, "data": state})
    except RuntimeError as error:
        return jsonify({"ok": False, "error": str(error)}), 409


@app.get("/api/export.xlsx")
def export_results():
    state = controller.snapshot()
    rows = state.get("results") or []
    if not rows:
        return jsonify({"ok": False, "error": "Пока нечего экспортировать в Excel."}), 409

    workbook = build_export_workbook(rows)
    filename = build_export_filename(str(state.get("query") or ""))
    return send_file(
        workbook,
        as_attachment=True,
        download_name=filename,
        mimetype=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )


atexit.register(controller.close_active_driver)


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.getenv("PORT", "5050")),
        debug=False,
    )
