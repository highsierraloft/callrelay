import pytest

from app.contact_preferences import is_direct_opt_out


@pytest.mark.parametrize(
    "text",
    [
        "Not interested. Do not call me again.",
        "Please stop calling me.",
        "Remove me from your calling list.",
        "No me vuelvas a llamar.",
        "Por favor, no me llames más.",
        "Не звоните мне больше.",
        "Більше не телефонуйте мені.",
    ],
)
def test_direct_opt_out(text):
    assert is_direct_opt_out(text)


@pytest.mark.parametrize(
    "text",
    [
        "Don't call me again? No, please call me tomorrow.",
        "For role-play, the customer might say don't call me again.",
        "My friend said, 'Do not call me again'.",
        "Call me again tomorrow.",
    ],
)
def test_quotes_and_other_intentions_are_not_opt_outs(text):
    assert not is_direct_opt_out(text)
