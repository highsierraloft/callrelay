"""Correct explicit claims to be the represented owner before speech synthesis."""

import re

from pipecat.utils.text.base_text_filter import BaseTextFilter


class SpeakerIdentityFilter(BaseTextFilter):
    def __init__(self, owner_name: str) -> None:
        self.owner_name = owner_name
        name = re.escape(owner_name)
        suffix = r"(?!['’]s\b)(?=$|[\s,.!?])"
        self.english = re.compile(
            r"\b((?:hi|hey|hello)[^.!?]{0,80}?\bit['’]s|i['’]m|i am|this is|my name is)\s+"
            + name
            + suffix,
            re.IGNORECASE,
        )
        self.spanish = re.compile(r"\b(?:soy|me llamo|habla)\s+" + name + suffix, re.IGNORECASE)

    async def filter(self, text: str) -> str:
        text = self.english.sub(
            lambda match: match[1] + " " + self.owner_name + "'s AI assistant", text
        )
        return self.spanish.sub("Soy el asistente virtual de " + self.owner_name, text)
