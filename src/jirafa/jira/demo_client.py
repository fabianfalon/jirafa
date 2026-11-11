"""Fake Jira client with made-up data. Same interface as the real one.

Lets you develop and try the agent without Jira access. Ticket content is in
Spanish on purpose, to resemble a real Spanish-speaking team's Jira.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from jirafa.jira.base import JiraClient, TicketNotFoundError, normalize_key
from jirafa.models import Comment, StatusCategory, Ticket, TicketDetail

_BASE_URL = "https://demo.atlassian.net"


@dataclass
class _DemoTicket:
    key: str
    title: str
    issue_type: str
    status: str
    category: StatusCategory
    priority: str
    days_ago: int
    description: str = ""
    comments: list[tuple[str, int, str]] = field(default_factory=list)  # (author, days_ago, body)


_TICKETS = [
    _DemoTicket(
        "DEMO-101",
        "Error 500 al exportar informes a PDF",
        "Bug",
        "En curso",
        StatusCategory.IN_PROGRESS,
        "Alta",
        1,
        "Al exportar informes con más de 1000 filas el servidor devuelve 500.",
        [
            ("Ana López", 3, "Lo reproduzco en staging."),
            ("Tú", 1, "Parece un timeout del generador."),
        ],
    ),
    _DemoTicket(
        "DEMO-102",
        "Añadir filtro por fecha en el listado de pedidos",
        "Historia",
        "En revisión",
        StatusCategory.IN_PROGRESS,
        "Media",
        4,
        "Como gestor quiero filtrar pedidos por rango de fechas.",
        [("Carlos Ruiz", 4, "PR abierta, pendiente de review.")],
    ),
    _DemoTicket(
        "DEMO-103",
        "Migrar el job nocturno a la nueva cola",
        "Tarea",
        "Bloqueado",
        StatusCategory.IN_PROGRESS,
        "Alta",
        12,
        "Mover el job de sincronización a la cola nueva.",
        [("Tú", 12, "Bloqueado: esperando credenciales del equipo de sistemas.")],
    ),
    _DemoTicket(
        "DEMO-104",
        "Actualizar dependencias del frontend",
        "Tarea",
        "Por hacer",
        StatusCategory.TO_DO,
        "Baja",
        20,
    ),
    _DemoTicket(
        "DEMO-105",
        "Documentar la API de clientes",
        "Tarea",
        "Por hacer",
        StatusCategory.TO_DO,
        "Media",
        6,
    ),
    _DemoTicket(
        "DEMO-099",
        "Corregir typo en el email de bienvenida",
        "Bug",
        "Hecho",
        StatusCategory.DONE,
        "Baja",
        9,
    ),
]


class DemoJiraClient(JiraClient):
    async def my_tickets(
        self, status: str | None = None, include_done: bool = False
    ) -> list[Ticket]:
        now = datetime.now(UTC)
        result = [
            self._to_ticket(t, now)
            for t in _TICKETS
            if (include_done or t.category != StatusCategory.DONE)
            and (status is None or t.status.lower() == status.strip().lower())
        ]
        return sorted(result, key=lambda t: t.updated, reverse=True)

    async def get_ticket(self, key: str) -> TicketDetail:
        key = normalize_key(key)
        demo = next((t for t in _TICKETS if t.key == key), None)
        if demo is None:
            raise TicketNotFoundError(f"Ticket {key} not found or not visible to you")
        now = datetime.now(UTC)
        return TicketDetail(
            **self._to_ticket(demo, now).model_dump(),
            description=demo.description,
            comments=[
                Comment(author=author, created=now - timedelta(days=ago), body=body)
                for author, ago, body in demo.comments
            ],
        )

    @staticmethod
    def _to_ticket(t: _DemoTicket, now: datetime) -> Ticket:
        return Ticket(
            key=t.key,
            title=t.title,
            issue_type=t.issue_type,
            status=t.status,
            status_category=t.category,
            priority=t.priority,
            updated=now - timedelta(days=t.days_ago),
            days_since_update=t.days_ago,
            url=f"{_BASE_URL}/browse/{t.key}",
        )
