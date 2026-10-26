import pytest

pytest.importorskip("pipecat")

from app.spoken_identity import SpeakerIdentityFilter


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hey Jordan, it's Alex.", "Hey Jordan, it's Alex's AI assistant."),
        (
            "I'm Alex, calling about the appointment.",
            "I'm Alex's AI assistant, calling about the appointment.",
        ),
        ("This is Alex.", "This is Alex's AI assistant."),
        ("Hola. Soy Alex.", "Hola. Soy el asistente virtual de Alex."),
        ("I'm Alex's assistant.", "I'm Alex's assistant."),
        ("I'm calling for Alex.", "I'm calling for Alex."),
        ("The owner's name? It's Alex.", "The owner's name? It's Alex."),
        ("Alex asked about the price.", "Alex asked about the price."),
    ],
)
async def test_owner_identity_is_corrected_without_rewriting_normal_references(text, expected):
    assert await SpeakerIdentityFilter("Alex").filter(text) == expected
