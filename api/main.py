"""Запуск: uvicorn api.main:app --reload"""
from fastapi import FastAPI

from api.routes import router

app = FastAPI(title="Мультиагентный репетитор по Python")
app.include_router(router)