"""r2b CLI 서브커맨드 모듈 (docs/specs/CLI-integration.md).

각 모듈은 ``register(app: typer.Typer) -> None``을 노출하고, 루트 앱 등록은
``app/cli.py``(메인 세션)가 수행한다. 모듈 간 상호 import는 금지한다.
"""
