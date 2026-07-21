import hashlib
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.schemas.reporting import ExecutionFactBundle, GovernanceReportContent


class HtmlReportRenderer:
    def __init__(self, template_root: Path | None = None) -> None:
        root = template_root or Path(__file__).resolve().parent / "templates"
        self.environment = Environment(
            loader=FileSystemLoader(root),
            autoescape=select_autoescape(default=True),
        )

    def render(
        self,
        facts: ExecutionFactBundle,
        content: GovernanceReportContent,
        version: int,
    ) -> tuple[str, str]:
        html = self.environment.get_template("governance-report.html.j2").render(
            facts=facts.model_dump(mode="json"),
            content=content.model_dump(mode="json"),
            report_version=version,
        )
        return html, hashlib.sha256(html.encode()).hexdigest()
