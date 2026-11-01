"""Единственное место, где создаётся модель. Смена провайдера = правка этого файла.

Клиент кэшируется: на один набор параметров создаётся один объект, поэтому токен
доступа GigaChat не запрашивается заново на каждый ход диалога.

Ключ берётся из .env (GIGACHAT_CREDENTIALS), в репозиторий не попадает.
"""
import os
from functools import lru_cache

from dotenv import load_dotenv
from langchain_gigachat.chat_models import GigaChat

load_dotenv()


@lru_cache(maxsize=None)
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
