def dashboard_app(database: str, prices: str) -> None:
    from pathlib import Path

    from copilot_metrics.dashboard import show_dashboard

    show_dashboard(Path(database), prices_path=Path(prices))
