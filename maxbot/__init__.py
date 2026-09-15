"""Плагин платформы MAX для гейтвея Hermes Agent."""

# module-level: так memory-лоадер находит провайдера по имени каталога.
# Guard: в окружении без hermes (pip install) импорт не должен валить entry-point скан.
try:
    from .chat_memory import ChatMemoryProvider  # noqa: F401
except ImportError:  # hermes не установлен — платформенная регистрация всё равно невозможна
    ChatMemoryProvider = None


def register(ctx):
    from .adapter import MaxAdapter, check_requirements, is_connected, validate_config
    from .contact_tool import register_contact_tool
    from .geo_tool import register_geo_tool
    from .group_tool import register_group_tool
    from .hooks import _apply_yaml_config, _env_enablement, _standalone_send, interactive_setup
    from .pin_tool import register_pin_tool
    from .sticker_tool import register_sticker_tool

    register_group_tool(ctx)    # max_group/max_channel
    register_geo_tool(ctx)      # max_geo
    register_sticker_tool(ctx)  # max_sticker
    register_contact_tool(ctx)  # max_contact
    register_pin_tool(ctx)      # max_pin

    ctx.register_platform(
        name="max",
        label="MAX",
        adapter_factory=MaxAdapter,
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["MAX_ACCESS_TOKEN"],
        install_hint="pip install maxbot (httpx и aiohttp уже в зависимостях)",
        max_message_length=4000,
        emoji="✈️",
        pii_safe=False,
        allow_update_command=True,
        platform_hint=(
            "You are chatting via MAX messenger (max.ru). MAX renders Markdown "
            "(bold, italic, strikethrough, underline, code, links, quotes), supports "
            "inline buttons, media attachments and streaming message previews. "
            "The audience is Russian-speaking — ALWAYS reply in Russian, including "
            "image/video analysis, tool results summaries and short answers."),
        setup_fn=interactive_setup,
        env_enablement_fn=_env_enablement,
        apply_yaml_config_fn=_apply_yaml_config,
        cron_deliver_env_var="MAX_HOME_CHANNEL",
        standalone_sender_fn=_standalone_send,
        allowed_users_env="MAX_ALLOWED_USERS",
        allow_all_env="MAX_ALLOW_ALL_USERS",
    )
