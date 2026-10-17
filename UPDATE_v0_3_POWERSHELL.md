# NGU HRM v0.3 · установка в Git-проект NGU_CRM

Архив `NGU_HRM_v0_3_Update.zip` содержит пути **от корня проекта**. Остановите сервер через Ctrl+C. Выполните команды по одной в PowerShell:

```powershell
cd C:\Users\bonda\PycharmProjects\NGU_CRM
git branch --show-current
git status --short
```

Ожидаемая ветка `feature/ngu-personnel`. Если есть непонятные изменения, сначала проверьте их; не перезаписывайте свои файлы. Если база `demo.db` существует, скопируйте её:

```powershell
if (Test-Path .\demo.db) {
    Copy-Item .\demo.db ".\demo_before_v0_3_$(Get-Date -Format 'yyyyMMdd_HHmmss').db"
}
Expand-Archive "$env:USERPROFILE\Downloads\NGU_HRM_v0_3_Update.zip" -DestinationPath . -Force
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m uvicorn ngu_crm.app:app --host 127.0.0.1 --port 8074
```

Открыть `http://127.0.0.1:8074/structure` и `/documents`. Порт 8074 выбран, чтобы не спутать интерфейс с ранее запущенной копией на 8073. Новые таблицы создаются при старте без удаления существующих демо-записей. `main.py`, `.git`, `.venv` и `demo.db` архив не содержит.

После успешной проверки остановите сервер и сохраните релиз:

```powershell
git add README.md UPDATE_v0_3_POWERSHELL.md docs ngu_crm templates static tests
git commit -m "Add NGU staffing and personnel document workspace"
git push -u origin feature/ngu-personnel
```
