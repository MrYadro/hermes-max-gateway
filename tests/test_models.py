from maxbot.models import parse_update


def _message_created(text="привет", chat_type="dialog", sender_id=42, chat_id=100):
    return {
        "update_type": "message_created",
        "marker": 555,
        "message": {
            "body": {"mid": "mid.1", "text": text, "attachments": []},
            "recipient": {"chat_id": chat_id, "chat_type": chat_type},
            "sender": {"user_id": sender_id, "name": "Иван", "username": "ivan"},
            "timestamp": 1737500130100,
        },
    }


def test_parse_message_created_dm():
    u = parse_update(_message_created())
    assert u.update_type == "message_created" and u.marker == 555
    m = u.message
    assert m.chat_id == 100 and m.chat_type == "dialog"
    assert m.body.mid == "mid.1" and m.body.text == "привет"
    assert m.sender.user_id == 42 and m.sender.name == "Иван"


def test_parse_message_with_attachments():
    d = _message_created()
    d["message"]["body"]["attachments"] = [
        {"type": "image", "payload": {"token": "t1", "url": "https://cdn/x.jpg"}},
        {"type": "location", "payload": {"latitude": 55.75, "longitude": 37.61}},
    ]
    atts = parse_update(d).message.body.attachments
    assert atts[0].type == "image" and atts[0].payload["token"] == "t1"
    assert atts[1].payload["latitude"] == 55.75


def test_parse_message_callback():
    d = {
        "update_type": "message_callback",
        "marker": 556,
        "callback": {
            "callback_id": "cb.1", "payload": "ea:once:7",
            "message": _message_created()["message"],
        },
    }
    u = parse_update(d)
    assert u.callback.callback_id == "cb.1" and u.callback.payload == "ea:once:7"
    assert u.callback.message.chat_id == 100


def test_parse_bot_started():
    u = parse_update({"update_type": "bot_started", "marker": 1, "chat_id": 900, "user": {"user_id": 5, "name": "Оля"}})
    assert u.chat_id == 900 and u.user.user_id == 5


def test_parse_tolerant_to_unknown_fields():
    d = _message_created()
    d["message"]["unknown_future_field"] = {"a": 1}
    assert parse_update(d).message.raw["unknown_future_field"] == {"a": 1}


def test_parse_location_flat_fields():
    """Координаты приходят плоскими полями вложения — парсер не должен их терять."""
    d = _message_created()
    d["message"]["body"]["attachments"] = [
        {"type": "location", "latitude": 11.111111, "longitude": 22.222222}]
    msg = parse_update(d).message
    loc = msg.body.attachments[0]
    assert loc.type == "location"
    assert loc.latitude == 11.111111 and loc.longitude == 22.222222
    assert loc.payload == {}
