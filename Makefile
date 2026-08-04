.PHONY: run test clean install

run:
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

test:
	pytest tests/ -v

clean:
	@echo Cleaning cache files...
	@for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul
	@for /r . %%f in (*.pyc) do @del /f /q "%%f" 2>nul
	@if exist .pytest_cache rd /s /q .pytest_cache 2>nul
	@if exist .mypy_cache rd /s /q .mypy_cache 2>nul
	@if exist .ruff_cache rd /s /q .ruff_cache 2>nul
	@echo Done.

install:
	pip install -r requirements.txt