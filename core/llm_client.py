"""Единственное место, где создаётся модель. Смена провайдера = правка этого файла.

Ключ берётся из .env (GIGACHAT_CREDENTIALS), в репозиторий не попадает.
"""
import os

from dotenv import load_dotenv
from langchain_gigachat.chat_models import GigaChat

load_dotenv()


def get_llm(temperature: float = 0.0, max_tokens: int = 800) -> GigaChat:
    return GigaChat(
        credentials=os.environ["GIGACHAT_CREDENTIALS"],
        scope="GIGACHAT_API_PERS",
        model="GigaChat-2",
        temperature=temperature,
        max_tokens=max_tokens,
        verify_ssl_certs=False,  # без сертификата Минцифры; для учёбы допустимо
    )


if __name__ == "__main__":
    print(get_llm().invoke("Привет! Ответь одним словом.").content)
