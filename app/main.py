from http.server import BaseHTTPRequestHandler, HTTPServer
import logging
import sys
import threading

from telegram.ext import ApplicationBuilder, CallbackQueryHandler, MessageHandler, filters

from .config import Settings
from .instance import single_instance
from .provider import GeminiProvider
from .repository import Repository
from .services import AgentService
from .ui import BotUI


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def start_health_check_server(port: int | None) -> HTTPServer | None:
    if port is None:
        return None
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logging.getLogger(__name__).info("Health check server running on port %s", port)
        return server
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not start health check server on port %s (%s)", port, exc)
        return None


def build_application(settings: Settings, provider=None, repository=None):
    repo = repository or Repository(settings.database)
    ai = provider or GeminiProvider(settings)
    service = AgentService(repo, ai, settings.daily_requests, settings.timezone)
    ui = BotUI(settings, repo, service)

    async def shutdown(application):
        await ai.close()

    async def error(update, context):
        logging.getLogger(__name__).error("Unhandled update error (%s)", type(context.error).__name__)

    # No ConversationHandler: our own per-owner locks guard SQLite-backed wizards.
    app = (ApplicationBuilder().token(settings.telegram_token).concurrent_updates(8)
           .connection_pool_size(16).post_shutdown(shutdown).build())
    app.add_handler(CallbackQueryHandler(ui.handle))
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE, ui.handle))
    app.add_error_handler(error)
    app.bot_data["ui"] = ui
    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # HTTP request URLs may contain the Telegram bot token.
    for name in ("httpx", "httpcore", "google.genai"):
        logging.getLogger(name).setLevel(logging.ERROR)
    try:
        settings = Settings.from_env()
        start_health_check_server(settings.port)
        with single_instance(settings.database):
            app = build_application(settings)
            app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=False)
    except (ValueError, OSError, RuntimeError) as exc:
        # Config errors name setting keys only, not their values.
        print("Setup error:", type(exc).__name__, "- Check .env and README_KH.md.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
