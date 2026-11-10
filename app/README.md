# DociFi — application source

This folder holds the Python package (`src/pcs_tool`), the rules (`config/`), the tests and the build scripts.
See the [project README](../README.md) for the overview, and [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)
for how the engine is organised.

```powershell
# from the repository root
py -3.14 -m venv app\.venv
app\.venv\Scripts\python.exe -m pip install -r app\requirements.txt
app\.venv\Scripts\python.exe -m pip install --no-deps rapidocr-onnxruntime==1.2.3
app\.venv\Scripts\python.exe -m pip install --no-deps -e app

app\.venv\Scripts\python.exe -m pytest app              # 123 tests
app\.venv\Scripts\python.exe -m ruff check app\src app\tests app\scripts
app\.venv\Scripts\python.exe app\scripts\build_exe.py   # -> app\dist\DociFi.exe
```
