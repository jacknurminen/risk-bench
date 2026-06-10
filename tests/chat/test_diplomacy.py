from risk_bench.chat.diplomacy import ChatMessage, ChatTranscript, MessageVisibility


def public_message(sender: str, text: str) -> ChatMessage:
    return ChatMessage(turn_number=1, sender=sender, visibility=MessageVisibility.PUBLIC, recipient=None, text=text)


def private_message(sender: str, recipient: str, text: str) -> ChatMessage:
    return ChatMessage(
        turn_number=1, sender=sender, visibility=MessageVisibility.PRIVATE, recipient=recipient, text=text
    )


def test_public_messages_are_visible_to_everyone():
    transcript = ChatTranscript()
    message = public_message(sender="alice", text="Let's all attack bob.")
    transcript.add(message=message)

    for player in ("alice", "bob", "carla"):
        assert transcript.visible_to(player_id=player) == [message]


def test_private_messages_are_visible_only_to_sender_and_recipient():
    transcript = ChatTranscript()
    message = private_message(sender="alice", recipient="bob", text="Truce in North America?")
    transcript.add(message=message)

    assert transcript.visible_to(player_id="alice") == [message]
    assert transcript.visible_to(player_id="bob") == [message]
    assert transcript.visible_to(player_id="carla") == []


def test_full_transcript_keeps_chronological_order_including_private_messages():
    transcript = ChatTranscript()
    first = public_message(sender="alice", text="Hello.")
    second = private_message(sender="bob", recipient="carla", text="Alice is bluffing.")
    transcript.add(message=first)
    transcript.add(message=second)

    assert transcript.full_transcript() == [first, second]
