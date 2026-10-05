"""FastAPI application for the SEPA Precios dashboard."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.db import close_pool, get_pool
from api.routes import (
    buscador,
    filters,
    health,
    lista,
    locales,
    ofertas,
    products,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    yield
    await close_pool()


app = FastAPI(title="SEPA Precios API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(filters.router, prefix="/api")
app.include_router(products.router, prefix="/api")
app.include_router(buscador.router, prefix="/api")
app.include_router(ofertas.router, prefix="/api")
app.include_router(locales.router, prefix="/api")
app.include_router(lista.router, prefix="/api")
