. "$PSScriptRoot\windows-common.ps1"

# NOTE: Use single-quoted Python string literals only. Windows PowerShell 5.1
# strips embedded double quotes when passing an argument to a native command.
$probe = @'
import os
import httpx

base_url = os.environ['MRAG_OLLAMA_URL'].rstrip('/')
model_name = os.environ['MRAG_EMBEDDING_MODEL']

response = httpx.post(
    f'{base_url}/api/embed',
    json={'model': model_name, 'input': 'GPU verification'},
    timeout=120,
)
response.raise_for_status()

running = httpx.get(f'{base_url}/api/ps', timeout=10)
running.raise_for_status()
models = running.json().get('models', [])
target = next(
    (
        item
        for item in models
        if item.get('name') == model_name or item.get('model') == model_name
    ),
    None,
)
if target is None:
    raise SystemExit(f'{model_name} is not listed by /api/ps after embedding')

size = int(target.get('size', 0))
size_vram = int(target.get('size_vram', 0))
if size_vram <= 0:
    raise SystemExit(
        f'{model_name} is running without GPU memory (size_vram={size_vram})'
    )

percentage = round(size_vram / size * 100, 1) if size else 0
print(
    f'Ollama GPU verified: model={model_name}, '
    f'size_vram={size_vram}, vram_share={percentage}%'
)
'@

Invoke-ProjectCompose "exec", "-T", "mrag-api", "python", "-c", $probe
