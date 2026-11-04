"""API pathways: routes in each framework, clients, and matching one to the other. Nothing here runs the code it reads."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ananke.plexus.intel import api
from ananke.plexus.intel._api_common import (
    Param,
    angle_params,
    brace_params,
    colon_params,
    regex_path,
    target_of,
)
from ananke.plexus.intel._api_script import scan
from ananke.plexus.intel.context import RepoContext


def write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def read(root: Path, files: dict[str, str]) -> dict[str, Any]:
    write(root, files)
    return api.analyze(RepoContext.open(root))


def routes_of(section: dict[str, Any]) -> dict[str, str | None]:
    return {f"{r['method']} {r['path']}": r["handler"] for r in section["routes"]}


def links_of(section: dict[str, Any]) -> dict[str, str | None]:
    return {f"{c['method']} {c['path']}": c.get("route") for c in section["clients"]}


# ---------------------------------------------------------------- FastAPI


def test_fastapi_joins_prefixes_through_files(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/routers/__init__.py": "",
            "app/routers/users.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter(prefix='/users')\n"
                "@router.get('/{user_id:int}')\n"
                "def read(user_id): ...\n"
                "@router.api_route('/x', methods=['PUT', 'patch'])\n"
                "def both(): ...\n"
            ),
            "app/v1.py": (
                "from fastapi import APIRouter\n"
                "from .routers import users\n"
                "api = APIRouter(prefix='/v1')\n"
                "api.include_router(users.router)\n"
            ),
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "from .v1 import api as v1\n"
                "app = FastAPI()\n"
                "app.include_router(v1, prefix='/api')\n"
            ),
        },
    )
    assert routes_of(found) == {
        "GET /api/v1/users/{user_id}": "function:app/routers/users.py:read",
        "PUT /api/v1/users/x": "function:app/routers/users.py:both",
        "PATCH /api/v1/users/x": "function:app/routers/users.py:both",
    }
    assert found["routes"][0]["framework"] == "fastapi"
    assert found["routes"][0]["evidence"].startswith("app/routers/users.py:")


def test_fastapi_prefix_from_a_constant_and_settings(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "config.py": (
                "class Settings:\n    API_V1: str = '/api/v1'\nsettings = Settings()\nHEALTH = '/health'\n"
            ),
            "main.py": (
                "from fastapi import FastAPI, APIRouter\n"
                "from config import settings, HEALTH\n"
                "app = FastAPI()\n"
                "router = APIRouter()\n"
                "@router.get('/items')\n"
                "def items(): ...\n"
                "@app.get(HEALTH)\n"
                "def health(): ...\n"
                "app.include_router(router, prefix=settings.API_V1)\n"
            ),
        },
    )
    assert set(routes_of(found)) == {"GET /api/v1/items", "GET /health"}


def test_an_unknown_prefix_is_left_out_and_said(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "main.py": (
                "import os\nfrom fastapi import FastAPI, APIRouter\n"
                "app = FastAPI()\nrouter = APIRouter()\n"
                "@router.get('/items')\ndef items(): ...\n"
                "app.include_router(router, prefix=os.environ['P'])\n"
            )
        },
    )
    assert found["routes"][0]["path"] == "/items"
    assert found["routes"][0]["prefix_unknown"] is True
    assert any("known only at run time" in limit for limit in found["limits"])


def test_handlers_inside_a_factory_or_method_keep_the_symbol_around_them(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "main.py": (
                "from fastapi import FastAPI\n"
                "from other import thing\n"
                "def create_app():\n"
                "    app = FastAPI()\n"
                "    @app.get('/a')\n"
                "    def a():\n"
                "        return thing()\n"
                "    return app\n"
                "class Api:\n"
                "    def __init__(self):\n"
                "        self.app = FastAPI()\n"
                "    def register(self):\n"
                "        @self.app.post('/b')\n"
                "        def b(): ...\n"
            ),
            "other.py": "def thing(): ...\n",
        },
    )
    by_path = {r["path"]: r for r in found["routes"]}
    assert by_path["/a"]["handler"] == "function:main.py:create_app"
    assert by_path["/a"]["handler_name"] == "a"
    assert by_path["/a"]["handler_lines"] == [6, 7]
    assert by_path["/b"]["handler"] == "method:main.py:Api.register"


def test_an_app_made_by_an_imported_factory_keeps_its_routes(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "factory.py": "from fastapi import FastAPI\ndef make():\n    app = FastAPI()\n    return app\n",
            "main.py": (
                "from fastapi import APIRouter\n"
                "from factory import make\n"
                "app = make()\n"
                "router = APIRouter()\n"
                "@router.get('/r')\ndef r(): ...\n"
                "app.include_router(router, prefix='/p')\n"
            ),
        },
    )
    assert set(routes_of(found)) == {"GET /p/r"}
    assert "unmounted" not in found["routes"][0]


def test_a_router_nothing_includes_is_marked(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "r.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter(prefix='/x')\n"
                "@router.get('/y')\ndef y(): ...\n"
            )
        },
    )
    assert found["routes"][0]["path"] == "/x/y"
    assert found["routes"][0]["unmounted"] is True
    assert any("nothing visibly includes" in limit for limit in found["limits"])


def test_routers_in_a_loop_are_followed(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "a.py": "from fastapi import APIRouter\nrouter = APIRouter()\n@router.get('/a')\ndef a(): ...\n",
            "b.py": "from fastapi import APIRouter\nrouter = APIRouter()\n@router.get('/b')\ndef b(): ...\n",
            "main.py": (
                "from fastapi import FastAPI\nimport a, b\n"
                "app = FastAPI()\n"
                "for r in [a.router, b.router]:\n"
                "    app.include_router(r, prefix='/api')\n"
            ),
        },
    )
    assert set(routes_of(found)) == {"GET /api/a", "GET /api/b"}


def test_routers_that_include_each_other_end(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "m.py": (
                "from fastapi import APIRouter\n"
                "a = APIRouter(prefix='/a')\nb = APIRouter(prefix='/b')\n"
                "a.include_router(b)\nb.include_router(a)\n"
                "@a.get('/x')\ndef x(): ...\n"
            )
        },
    )
    assert found["routes"]
    assert any("include each other" in limit for limit in found["limits"])


def test_a_decorator_on_something_else_is_not_a_route(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "m.py": (
                "import cache\n"
                "@cache.get('/not/a/route')\ndef x(): ...\n"
                "def deco(): ...\n"
                "# @app.get('/commented')\n"
                "text = \"@app.get('/in/a/string')\"\n"
            )
        },
    )
    assert found["routes"] == []
    assert found["status"] == "none"
    assert found["limits"][0].startswith("no routes")


# ---------------------------------------------------------------- Flask


def test_flask_blueprints_and_methods(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/views.py": (
                "from flask import Blueprint\n"
                "bp = Blueprint('v', __name__, url_prefix='/v')\n"
                "@bp.route('/items/<int:item_id>', methods=['GET', 'DELETE'])\n"
                "def item(item_id): ...\n"
                "@bp.route('/plain')\n"
                "def plain(): ...\n"
                "@bp.post('/new')\n"
                "def new(): ...\n"
            ),
            "pkg/other.py": (
                "from flask import Blueprint\n"
                "other = Blueprint('o', __name__, url_prefix='/replaced')\n"
                "@other.route('/z/<name>')\n"
                "def z(name): ...\n"
            ),
            "pkg/app.py": (
                "from flask import Flask\n"
                "from .views import bp\n"
                "from .other import other\n"
                "app = Flask(__name__)\n"
                "app.register_blueprint(bp, url_prefix='/api/v')\n"
                "app.register_blueprint(other, url_prefix='/o')\n"
                "def added(): ...\n"
                "app.add_url_rule('/added', view_func=added, methods=['POST'])\n"
            ),
        },
    )
    assert routes_of(found) == {
        "GET /api/v/items/{item_id}": "function:pkg/views.py:item",
        "DELETE /api/v/items/{item_id}": "function:pkg/views.py:item",
        "GET /api/v/plain": "function:pkg/views.py:plain",
        "POST /api/v/new": "function:pkg/views.py:new",
        "GET /o/z/{name}": "function:pkg/other.py:z",
        "POST /added": "function:pkg/app.py:added",
    }
    assert {r["framework"] for r in found["routes"]} == {"flask"}


def test_flask_routes_on_a_parameter(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "routes.py": (
                "from flask import jsonify\n"
                "def register(app):\n"
                "    @app.route('/ping')\n"
                "    def ping(): ...\n"
            )
        },
    )
    assert set(routes_of(found)) == {"GET /ping"}
    assert found["routes"][0]["framework"] == "flask"


def test_computed_methods_are_said(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "m.py": (
                "from flask import Flask\n"
                "app = Flask(__name__)\nMETHODS = compute()\n"
                "@app.route('/x', methods=METHODS)\ndef x(): ...\n"
            )
        },
    )
    assert set(routes_of(found)) == {"GET /x"}
    assert any("computed value" in limit for limit in found["limits"])


# ---------------------------------------------------------------- Django


def test_django_paths_includes_and_views(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "site/__init__.py": "",
            "site/urls.py": (
                "from django.urls import include, path, re_path\n"
                "from shop import views\n"
                "urlpatterns = [\n"
                "    path('shop/', include('shop.urls')),\n"
                "    path('inline/', include([path('a/', views.index)])),\n"
                "    re_path(r'^legacy/(?P<pk>\\d+)/$', views.index),\n"
                "    re_path(r'^weird/[a-z]+/$', views.index),\n"
                "]\n"
            ),
            "shop/__init__.py": "",
            "shop/urls.py": (
                "from django.urls import path\n"
                "from django.contrib.auth.decorators import login_required\n"
                "from . import views\n"
                "urlpatterns = [\n"
                "    path('', views.index),\n"
                "    path('orders/<int:pk>/', views.OrderView.as_view()),\n"
                "    path('wrapped/', login_required(views.index)),\n"
                "    path('only/', views.only_post),\n"
                "    path('plain-class/', views.Plain.as_view()),\n"
                "]\n"
            ),
            "shop/views.py": (
                "from django.views.decorators.http import require_POST\n"
                "def index(request): ...\n"
                "class OrderView:\n"
                "    def get(self, request, pk): ...\n"
                "    def post(self, request, pk): ...\n"
                "    def helper(self): ...\n"
                "class Plain:\n"
                "    template_name = 'x'\n"
                "@require_POST\n"
                "def only_post(request): ...\n"
            ),
        },
    )
    assert routes_of(found) == {
        "ANY /shop/": "function:shop/views.py:index",
        "ANY /inline/a/": "function:shop/views.py:index",
        "ANY /legacy/{pk}/": "function:shop/views.py:index",
        "GET /shop/orders/{pk}/": "method:shop/views.py:OrderView.get",
        "POST /shop/orders/{pk}/": "method:shop/views.py:OrderView.post",
        "ANY /shop/wrapped/": "function:shop/views.py:index",
        "POST /shop/only/": "function:shop/views.py:only_post",
        "ANY /shop/plain-class/": "class:shop/views.py:Plain",
    }
    assert any("re_path" in limit for limit in found["limits"])


def test_django_includes_that_form_a_cycle_end(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "a/__init__.py": "",
            "a/urls.py": "from django.urls import include, path\nurlpatterns = [path('b/', include('b.urls'))]\n",
            "b/__init__.py": "",
            "b/urls.py": (
                "from django.urls import include, path\n"
                "def v(request): ...\n"
                "urlpatterns = [path('a/', include('a.urls')), path('v/', v)]\n"
            ),
        },
    )
    assert "ANY /b/v/" in routes_of(found) or "ANY /a/v/" in routes_of(found)


def test_a_rest_framework_router_is_said(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "urls.py": (
                "from django.urls import include, path\n"
                "from rest_framework.routers import DefaultRouter\n"
                "router = DefaultRouter()\n"
                "urlpatterns = [path('api/', include(router.urls))]\n"
            )
        },
    )
    joined = " ".join(found["limits"])
    assert "include() calls couldn't be followed" in joined
    assert "REST framework" in joined


# ---------------------------------------------------------------- Express


def test_express_mounts_across_files(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "server.js": (
                "const express = require('express');\n"
                "const orders = require('./routes/orders');\n"
                "const app = express();\n"
                "app.use(express.json());\n"
                "app.use('/api/orders', auth, orders);\n"
                "app.use('/api/users', require('./routes/users'));\n"
                "app.get('port');\n"
                "app.get('/health', (req, res) => {\n  res.send('ok');\n});\n"
                "function auth(req, res, next) { next(); }\n"
            ),
            "routes/orders.js": (
                "const express = require('express');\n"
                "const controller = require('../controllers/orders');\n"
                "const router = express.Router();\n"
                "// router.get('/commented', x);\n"
                "router.get('/', controller.list);\n"
                "router.post('/:id/pay', check, controller.pay);\n"
                "router.route('/:id').get(controller.one).delete(controller.remove);\n"
                "function check(req, res, next) { next(); }\n"
                "module.exports = router;\n"
            ),
            "routes/users.js": (
                "import { Router } from 'express';\n"
                "import { show } from '../controllers/users';\n"
                "const router = Router();\n"
                "router.get('/:name?', show);\n"
                "export default router;\n"
            ),
            "controllers/orders.js": (
                "exports.list = function (req, res) {};\n"
                "exports.pay = function (req, res) {};\n"
                "exports.one = (req, res) => {};\n"
                "exports.remove = (req, res) => {};\n"
            ),
            "controllers/users.js": "export function show(req, res) {}\n",
        },
    )
    routes = routes_of(found)
    assert routes["GET /api/orders"] == "function:controllers/orders.js:list"
    assert routes["POST /api/orders/{id}/pay"] == "function:controllers/orders.js:pay"
    assert routes["GET /api/orders/{id}"] == "function:controllers/orders.js:one"
    assert routes["DELETE /api/orders/{id}"] == "function:controllers/orders.js:remove"
    assert routes["GET /api/users/{name}"] == "function:controllers/users.js:show"
    assert routes["GET /health"] == "file:server.js"
    assert "GET /api/orders/commented" not in routes
    assert not any(key.endswith(" port") for key in routes)
    inline = next(r for r in found["routes"] if r["path"] == "/health")
    assert inline["handler_name"] == "<inline>"
    assert inline["handler_lines"][1] > inline["handler_lines"][0]


def test_express_routes_on_a_parameter(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "main.js": "const express = require('express');\nconst a = express();\nrequire('./r')(a);\n",
            "r.js": "module.exports = function (app) {\n  app.post('/hook', function (req, res) {});\n};\n",
        },
    )
    assert routes_of(found)["POST /hook"] == "file:r.js"
    assert found["routes"][0]["handler_name"] == "<inline>"


def test_get_on_something_else_is_not_a_route(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "a.js": (
                "const cache = new Map();\n"
                "cache.get('/x', 1);\n"
                "router.get('/undeclared', h);\n"
                "app.get('/no/express/here', h);\n"
            )
        },
    )
    assert found["routes"] == []
    assert found["clients"] == []


# ---------------------------------------------------------------- NestJS


def test_nestjs_controllers(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "api/src/main.ts": (
                "async function bootstrap() {\n"
                "  const app = await NestFactory.create(AppModule);\n"
                "  app.setGlobalPrefix('api');\n"
                "}\n"
            ),
            "api/src/orders.controller.ts": (
                "import { Controller, Get, Post, Body, Param, UseGuards } from '@nestjs/common';\n"
                "@Controller('orders')\n"
                "export class OrdersController {\n"
                "  constructor(private readonly service: OrdersService) {}\n"
                "  @Get()\n"
                "  list() { return this.service.all(); }\n"
                "  @UseGuards(Auth)\n"
                "  @Get(':id')\n"
                "  async one(@Param('id') id: string): Promise<Order> { return null; }\n"
                "  @Post()\n"
                "  create(@Body() body: any) {}\n"
                "  helper() {}\n"
                "}\n"
            ),
        },
    )
    assert routes_of(found) == {
        "GET /api/orders": "method:api/src/orders.controller.ts:OrdersController.list",
        "GET /api/orders/{id}": "method:api/src/orders.controller.ts:OrdersController.one",
        "POST /api/orders": "method:api/src/orders.controller.ts:OrdersController.create",
    }
    assert {r["framework"] for r in found["routes"]} == {"nestjs"}


def test_a_controller_decorator_from_elsewhere_is_ignored(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "a.ts": "import { Controller, Get } from './mine';\n@Controller('x')\nclass A { @Get() b() {} }\n"
        },
    )
    assert found["routes"] == []


# ---------------------------------------------------------------- OpenAPI


def test_openapi_files_are_listed_and_gaps_noted(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "m.py": "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/items/{id}')\ndef i(id): ...\n",
            "docs/openapi.yaml": (
                "openapi: 3.0.0\n"
                "info:\n  title: Shop\n  version: 1\n"
                "servers:\n  - url: https://example.com\n"
                "paths:\n"
                "  /items/{item_id}:\n    get:\n      summary: x\n"
                "  /ghost:\n    post:\n      summary: x\n    delete:\n      summary: y\n"
            ),
            "swagger.json": json.dumps(
                {
                    "swagger": "2.0",
                    "info": {"title": "Old"},
                    "basePath": "/items",
                    "paths": {"/{id}": {"get": {}}},
                }
            ),
            "openapi-broken.json": "{not json",
            "swagger-settings.json": json.dumps({"hello": "world"}),
        },
    )
    specs = {s["path"]: s for s in found["specs"]}
    assert set(specs) == {"docs/openapi.yaml", "swagger.json"}
    assert specs["docs/openapi.yaml"]["title"] == "Shop"
    assert specs["docs/openapi.yaml"]["routes"] == 3
    assert specs["docs/openapi.yaml"]["missing_from_code"] == ["DELETE /ghost", "POST /ghost"]
    assert specs["swagger.json"]["missing_from_code"] == []
    assert set(routes_of(found)) == {"GET /items/{id}"}, "a spec never invents a route"
    assert any("no matching route" in limit for limit in found["limits"])


# ---------------------------------------------------------------- clients


def test_fetch_forms(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "web/api.ts": (
                "const BASE = '/api';\n"
                "export const a = () => fetch('/plain');\n"
                "export const b = (id: number) => fetch(`/orders/${id}/items/${encodeURIComponent(id)}`);\n"
                "export const c = (o: any) => fetch(BASE + '/concat/' + o.id, { method: 'PUT' });\n"
                "export const d = () => window.fetch(`${BASE}/win`, { method: 'delete' });\n"
                "export const e = (opts: any) => fetch('/opaque', opts);\n"
                "export const f = (q: string) => fetch(`/search?q=${q}`);\n"
                "export const g = (query: string) => fetch(`/glued${query}`);\n"
                "// fetch('/commented')\n"
                "export const h = 'fetch(\"/in/a/string\")';\n"
                "export const i = (u: string) => fetch(u);\n"
                "export const j = () => fetch('./data.json');\n"
                "export const k = () => fetch('data:text/plain,hi');\n"
                "export const l = () => { const url = '/local'; return fetch(url); };\n"
                "export const m = () => { const url = `/${'two'}`; return fetch(url); };\n"
            ),
        },
    )
    links = {f"{c['method']} {c['path']}" for c in found["clients"]}
    assert links == {
        "GET /plain",
        "GET /orders/{id}/items/{id}",
        "PUT /api/concat/{id}",
        "DELETE /api/win",
        "UNKNOWN /opaque",
        "GET /search",
        "GET /glued",
        "GET /data.json",
        "GET /local",
        "GET /two",
    }
    statics = [c for c in found["clients"] if c.get("static")]
    assert [c["path"] for c in statics] == ["/data.json"]
    assert "/data.json" not in {c["path"] for c in found["unmatched_clients"]}
    assert any("known only at run time" in limit for limit in found["limits"])


def test_a_local_function_named_fetch_is_not_http(tmp_path: Path) -> None:
    found = read(tmp_path, {"a.js": "function fetch(x) { return x; }\nfetch('/not/http');\n"})
    assert found["clients"] == []


def test_axios_instance_from_another_file_with_its_base(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "src/lib/http.ts": (
                "import axios from 'axios';\n"
                "export const http = axios.create({ timeout: 5, baseURL: '/api/' });\n"
                "const other = axios.create({ baseURL: process.env.API_URL || '/fallback' });\n"
                "export default other;\n"
            ),
            "src/lib/index.ts": "export { http as client } from './http';\n",
            "src/a.ts": (
                "import axios from 'axios';\n"
                "import api, { http } from './lib/http';\n"
                "import { client } from './lib';\n"
                "export const one = () => http.get('/one');\n"
                "export const two = (id: string) => api.post(`/two/${id}`);\n"
                "export const three = () => client.delete('three');\n"
                "export const four = () => axios.put('/four');\n"
                "export const five = () => axios({ url: '/five', method: 'patch' });\n"
                "export const six = () => axios.get<Order[]>('/six');\n"
                "export const seven = () => http.request({ url: '/seven', method: 'POST' });\n"
                "export const eight = () => axios('/eight');\n"
                "export const nine = () => http.get('https://elsewhere.example.com/nine');\n"
            ),
        },
    )
    links = {f"{c['method']} {c['path']}": c["caller"] for c in found["clients"]}
    assert links == {
        "GET /api/one": "function:src/a.ts:one",
        "POST /fallback/two/{id}": "function:src/a.ts:two",
        "DELETE /api/three": "function:src/a.ts:three",
        "PUT /four": "function:src/a.ts:four",
        "PATCH /five": "function:src/a.ts:five",
        "GET /six": "function:src/a.ts:six",
        "POST /api/seven": "function:src/a.ts:seven",
        "GET /eight": "function:src/a.ts:eight",
        "GET /nine": "function:src/a.ts:nine",
    }
    external = [c for c in found["clients"] if c.get("external")]
    assert [c["host"] for c in external] == ["elsewhere.example.com"]


def test_angular_http_client(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "src/environments/environment.ts": "export const environment = { api: { base: '/api/v1' }, url: '/api' };\n",
            "src/app/service.ts": (
                "import { HttpClient } from '@angular/common/http';\n"
                "import { environment } from '../environments/environment';\n"
                "export class Service {\n"
                "  private base = environment.api.base;\n"
                "  private token = '';\n"
                "  private readonly other: string = 'http://localhost:8000/v2';\n"
                "  constructor(private http: HttpClient) {}\n"
                "  list() { return this.http.get<Item[]>(`${this.base}/items`); }\n"
                "  one(id?: string) {\n"
                "    const key = id || this.token;\n"
                "    return this.http.get<Item<{ a: string }>>(`${this.base}/items/${key}`);\n"
                "  }\n"
                "  two() { return this.http.post(`${this.other}/two`, {}); }\n"
                "}\n"
                "export class NotHttp {\n"
                "  constructor(private store: Store) {}\n"
                "  list() { return this.store.get('/not/http'); }\n"
                "}\n"
            ),
        },
    )
    assert {f"{c['method']} {c['path']}": c["framework"] for c in found["clients"]} == {
        "GET /api/v1/items": "angular",
        "GET /api/v1/items/{id}": "angular",
        "POST /v2/two": "angular",
    }


def test_python_clients(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "m.py": (
                "import os\nimport requests\nimport httpx\n"
                "BASE = os.getenv('BASE', 'http://localhost:9000/api')\n"
                "UNSET = os.environ['UNSET']\n"
                "def a(): return requests.get(f'{BASE}/a')\n"
                "def b(pk): return requests.post(BASE + '/b/' + str(pk), json={})\n"
                "def c(): return httpx.delete('https://api.example.com/c')\n"
                "def d(): return requests.request('PUT', f'{BASE}/d')\n"
                "def e(url): return requests.get(url)\n"
                "def f(base='http://localhost:1/x'): return requests.get(f'{base}/f')\n"
                "def g(): return requests.get(f'{UNSET}/g')\n"
                "def h():\n"
                "    with httpx.Client(base_url='/w') as client:\n"
                "        return client.get('/h')\n"
                "class K:\n"
                "    def __init__(self):\n"
                "        self.http = httpx.Client(base_url=BASE)\n"
                "    def i(self, i): return self.http.patch(f'/i/{i}')\n"
                "def j():\n"
                "    url = f'{BASE}/j'\n"
                "    return requests.get(url)\n"
                "def not_http(d): return d.get('/k')\n"
            ),
        },
    )
    assert {f"{c['method']} {c['path']}": c["caller"] for c in found["clients"]} == {
        "GET /api/a": "function:m.py:a",
        "POST /api/b/{pk}": "function:m.py:b",
        "DELETE /c": "function:m.py:c",
        "PUT /api/d": "function:m.py:d",
        "GET /x/f": "function:m.py:f",
        "GET /g": "function:m.py:g",
        "GET /w/h": "function:m.py:h",
        "PATCH /api/i/{i}": "method:m.py:K.i",
        "GET /api/j": "function:m.py:j",
    }
    by_path = {c["path"]: c for c in found["clients"]}
    assert by_path["/g"]["base_unknown"] is True
    assert by_path["/c"]["external"] is True
    assert any("m.py:" in limit and "known only at run time" in limit for limit in found["limits"])


# ---------------------------------------------------------------- matching


BACKEND = {
    "b.py": (
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.get('/customers/{customer_id}/reports')\ndef r1(customer_id): ...\n"
        "@app.get('/customers/{customer_id}/sales')\ndef r2(customer_id): ...\n"
        "@app.get('/customers/reports/sales')\ndef r3(): ...\n"
        "@app.post('/customers/reports/sales')\ndef r4(): ...\n"
        "@app.get('/items/{a}')\ndef i1(a): ...\n"
        "@app.get('/{anything:path}')\ndef spa(anything): ...\n"
        "@app.get('/files/{rest:path}')\ndef files(rest): ...\n"
        "@app.get('/same')\ndef same(): ...\n"
        "@app.post('/same')\ndef same2(): ...\n"
        "@app.delete('/one')\ndef one(): ...\n"
    ),
}


def clients_for(tmp_path: Path, script: str, extra: dict[str, str] | None = None) -> dict[str, Any]:
    return read(tmp_path, {**BACKEND, "c.ts": script, **(extra or {})})


def test_a_literal_beats_a_parameter(tmp_path: Path) -> None:
    found = clients_for(
        tmp_path,
        "export const a = () => fetch('/customers/reports/sales');\n"
        "export const b = (id: string) => fetch(`/customers/${id}/sales`);\n"
        "export const c = () => fetch('/customers/42/sales');\n"
        "export const d = () => fetch('/customers/reports/sales', { method: 'POST' });\n",
    )
    assert [c["route"] for c in found["clients"]] == [
        "route:GET /customers/reports/sales",
        "route:GET /customers/{customer_id}/sales",
        "route:GET /customers/{customer_id}/sales",
        "route:POST /customers/reports/sales",
    ]
    assert found["unmatched_clients"] == []


def test_a_parameter_matches_any_name_but_not_a_literal(tmp_path: Path) -> None:
    found = clients_for(
        tmp_path,
        "export const a = (other: string) => fetch(`/items/${other}`);\n"
        "export const b = (x: string) => fetch(`/customers/reports/${x}`);\n",
    )
    assert found["clients"][0]["route"] == "route:GET /items/{a}"
    assert "route" not in found["clients"][1]
    assert [c["path"] for c in found["unmatched_clients"]] == ["/customers/reports/{x}"]


def test_the_method_must_match(tmp_path: Path) -> None:
    found = clients_for(tmp_path, "export const a = () => fetch('/items/1', { method: 'POST' });\n")
    assert "route" not in found["clients"][0]
    assert found["unmatched_clients"][0]["path"] == "/items/1"


def test_an_unknown_method_matches_only_a_single_route(tmp_path: Path) -> None:
    found = clients_for(
        tmp_path,
        "export const a = (o: any) => fetch('/same', o);\n"
        "export const b = (o: any) => fetch('/one', o);\n",
    )
    assert [c.get("route") for c in found["clients"]] == [None, "route:DELETE /one"]


def test_a_catch_all_at_the_root_matches_nothing(tmp_path: Path) -> None:
    found = clients_for(
        tmp_path,
        "export const a = () => fetch('/nowhere');\nexport const b = () => fetch('/files/x');\n",
    )
    assert "route" not in found["clients"][0]
    assert found["clients"][1]["route"] == "route:GET /files/{rest}"


def test_a_trailing_slash_does_not_matter(tmp_path: Path) -> None:
    found = clients_for(tmp_path, "export const a = () => fetch('/same/');\n")
    assert found["clients"][0]["route"] == "route:GET /same"


def test_other_hosts_are_listed_and_never_matched(tmp_path: Path) -> None:
    found = clients_for(
        tmp_path,
        "export const a = () => fetch('https://api.github.com/same');\n"
        "export const b = () => fetch('http://localhost:8000/same');\n"
        "export const c = () => fetch('http://backend:8000/same');\n"
        "export const d = () => fetch('//cdn.example.com/same');\n",
    )
    rows = found["clients"]
    assert rows[0]["external"] is True and rows[0]["host"] == "api.github.com"
    assert "route" not in rows[0]
    assert rows[1]["route"] == rows[2]["route"] == "route:GET /same"
    assert rows[3]["external"] is True
    assert found["unmatched_clients"] == []


def test_an_unknown_base_is_marked_when_matched(tmp_path: Path) -> None:
    found = clients_for(
        tmp_path,
        "const API = import.meta.env.VITE_API;\n"
        "export const a = () => fetch(`${API}/same`);\n"
        "export const b = (x: string) => fetch(`${API}/${x}`);\n",
    )
    assert len(found["clients"]) == 1
    assert found["clients"][0]["route"] == "route:GET /same"
    assert found["clients"][0]["base_unknown"] is True


def test_the_same_path_in_two_services_goes_to_the_nearest(tmp_path: Path) -> None:
    app = "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/same')\ndef same(): ...\n"
    found = read(
        tmp_path,
        {
            "a/api/main.py": app,
            "b/api/main.py": app,
            "a/web/c.ts": "export const x = () => fetch('/same');\n",
            "b/web/c.ts": "export const y = () => fetch('/same');\n",
            "c.ts": "export const z = () => fetch('/same');\n",
        },
    )
    ids = {r["id"] for r in found["routes"]}
    assert len(ids) == 2, "two services, two routes, each with its own id"
    assert "route:GET /same" in ids
    assert any("(b/api/main.py:3)" in i for i in ids)
    by_file = {c["evidence"].split(":")[0]: c for c in found["clients"]}
    assert by_file["a/web/c.ts"]["route"] == "route:GET /same"
    assert "b/api/main.py" in by_file["b/web/c.ts"]["route"]
    assert by_file["c.ts"]["ambiguous"] is True
    assert "ambiguous" not in by_file["a/web/c.ts"]
    assert any("repeat a method and path" in limit for limit in found["limits"])


# ---------------------------------------------------------------- proxies


@pytest.mark.parametrize(
    ("name", "config"),
    [
        (
            "web/vite.config.ts",
            "export default defineConfig({ server: { proxy: { '/backend': {\n"
            "  target: 'http://localhost:8000',\n"
            "  rewrite: (path) => path.replace(/^\\/backend/, ''),\n"
            "} } } });\n",
        ),
        (
            "web/proxy.conf.json",
            '{"/backend": {"target": "http://localhost:8000", "pathRewrite": {"^/backend": ""}}}\n',
        ),
        (
            "web/nginx.conf",
            "server {\n  location /backend/ {\n    proxy_pass http://api:8000/;\n  }\n}\n",
        ),
    ],
)
def test_a_proxy_that_strips_its_prefix_is_followed_when_stated(
    tmp_path: Path, name: str, config: str
) -> None:
    found = read(
        tmp_path,
        {**BACKEND, name: config, "web/c.ts": "export const a = () => fetch('/backend/same');\n"},
    )
    client = found["clients"][0]
    assert client["path"] == "/backend/same"
    assert client["route"] == "route:GET /same"
    assert client["proxy"]["strips"] == "/backend"
    assert client["proxy"]["evidence"].startswith(name)


@pytest.mark.parametrize(
    ("name", "config"),
    [
        (
            "web/vite.config.ts",
            "export default { server: { proxy: { '/backend': 'http://x' } } };\n",
        ),
        ("web/nginx.conf", "location /backend/ {\n  proxy_pass http://api:8000;\n}\n"),
        ("web/nginx.conf", "location /backend/ {\n  proxy_pass http://api:8000/other/;\n}\n"),
        ("other/nginx.conf", "location /backend/ {\n  proxy_pass http://api:8000/;\n}\n"),
    ],
)
def test_a_prefix_nothing_says_is_stripped_stays_unmatched(
    tmp_path: Path, name: str, config: str
) -> None:
    found = read(
        tmp_path,
        {**BACKEND, name: config, "web/c.ts": "export const a = () => fetch('/backend/same');\n"},
    )
    assert "route" not in found["clients"][0]
    assert len(found["unmatched_clients"]) == 1


# ---------------------------------------------------------------- what isn't read


def test_tests_fixtures_notebooks_and_templates_are_left_out_and_said(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "tests/test_a.py": (
                "import requests\nfrom fastapi import FastAPI\napp = FastAPI()\n"
                "@app.get('/in-test')\ndef t(): requests.get('/x')\n"
            ),
            "src/a.test.ts": "fetch('/test');\n",
            "web/fixtures/f.ts": "fetch('/fixture');\n",
            "n.ipynb": json.dumps({"cells": [], "metadata": {}}),
            "p.vue": "<script>fetch('/vue')</script>\n",
        },
    )
    assert found["routes"] == [] and found["clients"] == []
    joined = " ".join(found["limits"])
    assert "test and fixture files" in joined
    assert "notebooks" in joined
    assert ".vue" in joined


def test_unsupported_frameworks_are_said(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "s.py": (
                "from http.server import BaseHTTPRequestHandler\n"
                "class H(BaseHTTPRequestHandler):\n"
                "    def do_GET(self):\n"
                "        if self.path == '/x': pass\n"
            )
        },
    )
    assert found["status"] == "none"
    assert any("http.server" in limit for limit in found["limits"])


def test_malformed_files_become_limits(tmp_path: Path) -> None:
    found = read(
        tmp_path,
        {
            "broken.py": "from fastapi import FastAPI\ndef (:\n",
            "deep.py": "import fastapi\nx = " + "(" * 5000 + "1" + ")" * 5000 + "\n",
            "binary.py": "\x00\x01 fastapi",
            "broken.ts": "fetch(`${`${`${\nexport class { @@@ ((( ''' \n",
            "openapi.yaml": ":\n:\n  - [\n",
            "openapi.json": "[" * 5000,
            "swagger.json": '"text"',
            "nginx.conf": "location\nproxy_pass\nlocation /x\n",
            "vite.config.ts": "'/api': {",
            "ok.py": "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/fine')\ndef f(): ...\n",
        },
    )
    assert "GET /fine" in routes_of(found)
    assert any("don't parse" in limit and "broken.py" in limit for limit in found["limits"])


def test_output_is_stable_and_sorted(tmp_path: Path) -> None:
    write(tmp_path, {**BACKEND, "c.ts": "export const a = () => fetch('/same');\n"})
    first = api.analyze(RepoContext.open(tmp_path))
    second = api.analyze(RepoContext.open(tmp_path))
    assert first == second
    keys = [(r["path"], r["method"]) for r in first["routes"]]
    assert keys == sorted(keys)
    assert all("_file" not in r for r in first["routes"])


def test_nothing_runs_and_nothing_is_written(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "boom.py": (
                "open('ran.txt', 'w').write('x')\n"
                "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/x')\ndef x(): ...\n"
            ),
            "boom.js": "require('fs').writeFileSync('ran2.txt', 'x');\n",
        },
    )
    before = sorted(p.name for p in tmp_path.iterdir())
    assert api.analyze(RepoContext.open(tmp_path))["routes"]
    assert sorted(p.name for p in tmp_path.iterdir()) == before


# ---------------------------------------------------------------- pieces


def test_parameter_forms() -> None:
    assert angle_params("/a/<int:id>/<name>/<path:rest>") == "/a/{id}/{name}/{rest}"
    assert brace_params("/a/{id:int}/{name}") == "/a/{id}/{name}"
    assert colon_params("/a/:id/:name?/*") == "/a/{id}/{name}/{*}"
    assert colon_params("/a/:id(\\d+)") == "/a/{id}"
    assert regex_path(r"^a/(?P<pk>\d+)/$") == "a/{pk}/"
    assert regex_path(r"^a/\.well-known/$") == "a/.well-known/"
    assert regex_path(r"^a/[0-9]+/$") is None
    assert regex_path("(?P<open") is None
    assert regex_path(r"(?P<x>(unclosed") is None


def test_targets() -> None:
    assert target_of([Param("x")]) is None
    assert target_of([]) is None
    assert target_of(None) is None
    assert target_of(["mailto:a@b.c"]) is None
    assert target_of(["data:text/plain,x"]) is None
    only = target_of(["/a/", Param("id"), "?x=1#frag"])
    assert only is not None and only.path == "/a/{id}"
    assert target_of(["/a", Param("b"), "/"]) is not None
    assert target_of([Param("base"), "/", Param("id")]) is None
    named = target_of(["https://user@host.example.com:8443/x"])
    assert named is not None and named.host == "host.example.com" and named.path == "/x"
    bare = target_of(["http://", Param("host"), ":8000/x"])
    assert bare is not None and bare.base_unknown and bare.path == "/x"
    assert target_of(["/a/" * 100]) is None


def test_scan_keeps_template_text_and_lines() -> None:
    tokens = scan("// c\nconst a = `x${b}y${`n${d}`}z`;\n/* m\n */ fetch('/u');\n")
    assert [t.text for t in tokens if t.kind == "quasi"] == ["x", "y", "n", "", "z"]
    assert next(t for t in tokens if t.text == "fetch").line == 4
    assert [t.text for t in scan("a = /x\\/y/.test(b); c = d / e / f;") if t.kind == "regex"] == [
        "/x\\/y/"
    ]


# ---------------------------------------------------------------- hostile input


def best_of_three(work: Callable[[], object]) -> float:
    times = []
    for _ in range(3):
        start = time.perf_counter()
        work()
        times.append(time.perf_counter() - start)
    return min(times)


SIZE = 100_000
TEXT_INPUTS: dict[str, str] = {
    "calls": "fetch(" * (SIZE // 6),
    "nested_generics": "http.get<" * (SIZE // 9),
    "open_templates": "`${" * (SIZE // 3),
    "backticks": "`" * SIZE,
    "single_quotes": "'" * SIZE,
    "double_quotes": '"' * SIZE,
    "slashes": "/" * SIZE,
    "regex_like": "= /" * (SIZE // 3),
    "backslashes": "\\" * SIZE,
    "open_braces": "{" * SIZE,
    "close_braces": "}" * SIZE,
    "parens": "(" * SIZE,
    "at_signs": "@Controller(" * (SIZE // 12),
    "dollars": "${" * (SIZE // 2),
    "unclosed_comment": "/*" * (SIZE // 2),
    "words": "a." * (SIZE // 2),
    "lines_of_quotes": "'\n" * (SIZE // 2),
}


@pytest.mark.parametrize("name", sorted(TEXT_INPUTS))
def test_scanning_stays_linear(name: str) -> None:
    text = TEXT_INPUTS[name]
    assert best_of_three(lambda: scan(text)) < 1.0


@pytest.mark.parametrize(
    ("label", "work"),
    [
        ("angle", lambda: angle_params("<" * SIZE)),
        ("angle_open", lambda: angle_params("<a" * (SIZE // 2))),
        ("brace", lambda: brace_params("{" * SIZE)),
        ("brace_open", lambda: brace_params("{a" * (SIZE // 2))),
        ("colon", lambda: colon_params(":a/" * (SIZE // 3))),
        ("regex_groups", lambda: regex_path("(?P<" * (SIZE // 4))),
        ("regex_groups_closed", lambda: regex_path("(?P<a>x)" * (SIZE // 8))),
        ("regex_open_parens", lambda: regex_path("(?P<a>" + "(" * SIZE)),
        ("target_slashes", lambda: target_of(["/" * SIZE])),
        ("target_segments", lambda: target_of(["/a" * (SIZE // 2)])),
        ("target_query", lambda: target_of(["?" * SIZE])),
        (
            "nginx",
            lambda: api._read_nginx(
                "n.conf", "location /a/ {\nproxy_pass http://a/;\n" * (SIZE // 40)
            ),
        ),
        ("proxy", lambda: api._read_proxy_config("p.js", "'/a': {" * (SIZE // 7))),
        (
            "yaml",
            lambda: api._yaml_operations(
                "openapi: 3\npaths:\n" + "  /a:\n    get:\n" * (SIZE // 16)
            ),
        ),
        ("yaml_one_line", lambda: api._yaml_operations("a" * SIZE)),
        ("json_deep", lambda: api._json_operations("[" * SIZE)),
    ],
)
def test_each_small_parser_stays_linear(label: str, work: Callable[[], object]) -> None:
    assert best_of_three(work) < 1.0, label


HOSTILE_REPOS: dict[str, dict[str, str]] = {
    "many_fetches": {"a.js": "".join(f'function f{i}(){{fetch("/a{i}")}}\n' for i in range(3000))},
    "same_local_name": {
        "a.js": 'function f(){const url="/x";return fetch(url)}\n' * 2200,
    },
    "constant_chain": {
        "a.js": "const a0='/x';\n"
        + "".join(f"const a{i + 1}=a{i}+a{i}+a{i};\n" for i in range(3000))
        + "fetch(a3000);\n" * 500,
    },
    "self_reference": {"a.js": "const a=b;const b=a;\n" + "fetch(a);\n" * 8000},
    "big_object": {
        "a.js": "fetch('/x',{" + "a:1,\n" * 18000 + "});\n" + "fetch('/y',{...o});\n" * 2000,
    },
    "many_middlewares": {
        "a.js": "const express=require('express');const app=express();\n"
        + "app.use('/a',"
        + "m,\n" * 30000
        + "m);\n"
    },
    "express_fan_out": {
        "a.js": "const express=require('express');\n"
        + "".join(f"const r{i}=express.Router();\n" for i in range(60))
        + "".join(f"r{i}.use('/x',r{i + 1});r{i}.use('/y',r{i + 1});\n" for i in range(59))
        + "r59.get('/end',h);\n"
    },
    "nest_fan_out": {
        "a.ts": "import {Controller,Get} from '@nestjs/common';\n@Controller(['a','b','c','d'])\nclass A{\n"
        + "@Get(['a','b','c','d'])\n" * 4000
        + "x(){}\n}\n",
    },
    "many_python_routes": {
        "m.py": "from fastapi import FastAPI\napp = FastAPI()\n"
        + "".join(f"@app.get('/r{i}')\ndef r{i}(): ...\n" for i in range(3000))
    },
    "python_same_shape": {
        "m.py": "from fastapi import FastAPI\nimport requests\napp = FastAPI()\n"
        + "".join(f"@app.get('/r/{{a{i}}}')\ndef r{i}(): ...\n" for i in range(1500))
        + "".join(f"def c{i}(): requests.get('/r/1')\n" for i in range(1500)),
    },
    "python_self_attrs": {
        "m.py": "import requests\n"
        + "".join(f"class C{i}:\n    a{i} = '/x'\n" for i in range(1500))
        + "class D:\n"
        + "".join(f"    def m{i}(self): requests.get(self.missing)\n" for i in range(1500)),
    },
    "python_mounts": {
        "m.py": "from fastapi import APIRouter\n"
        + "".join(f"r{i} = APIRouter(prefix='/p')\n" for i in range(60))
        + "".join(
            f"r{i}.include_router(r{i + 1})\nr{i}.include_router(r{i + 1}, prefix='/q')\n"
            for i in range(59)
        )
        + "@r59.get('/end')\ndef end(): ...\n",
    },
    "python_long_chain": {
        "m.py": "import requests\nx = " + ".".join(["a"] * 2000) + ".get('/x')\n" * 1,
    },
    "python_concat": {"m.py": "import requests\nrequests.get(" + "+".join(["'a'"] * 3000) + ")\n"},
    "django_includes": {
        **{
            f"p{i}/urls.py": (
                "from django.urls import include, path\n"
                f"urlpatterns = [path('a/', include('p{i + 1}.urls')), path('b/', include('p{i + 1}.urls'))]\n"
            )
            for i in range(14)
        },
        **{f"p{i}/__init__.py": "" for i in range(15)},
        "p14/urls.py": "from django.urls import path\ndef v(r): ...\nurlpatterns = [path('v/', v)]\n",
    },
    "django_regex": {
        "urls.py": "from django.urls import re_path\ndef v(r): ...\nurlpatterns = ["
        + "re_path(r'(?P<a>x)(?P<b>y)', v),\n" * 2500
        + "]\n"
    },
    "spec_many_paths": {"openapi.yaml": "openapi: 3\npaths:\n" + "  /a:\n    get: {}\n" * 5000},
}


@pytest.mark.parametrize("name", sorted(HOSTILE_REPOS))
def test_hostile_repositories_stay_fast(tmp_path: Path, name: str) -> None:
    write(tmp_path, HOSTILE_REPOS[name])
    ctx = RepoContext.open(tmp_path)  # the graph has its own tests; this times only the analysis
    assert best_of_three(lambda: api.analyze(ctx)) < 1.0
    assert api.analyze(ctx)["status"] in {"found", "none"}
