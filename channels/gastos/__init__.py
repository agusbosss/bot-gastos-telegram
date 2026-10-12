"""Canal 'gastos': el bot que registra gastos y tickets."""
from channels.base import Channel
from channels.gastos.handler import GastosHandler

GASTOS_CHANNEL = Channel(
    key="gastos",
    display_name="Gastos y Tickets",
    handler=GastosHandler(),
    token_env="GASTOS_BOT_TOKEN",
    chat_id_env="GASTOS_CHAT_ID",
    enabled=True,
)
