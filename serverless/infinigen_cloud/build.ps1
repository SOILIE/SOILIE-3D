$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
Push-Location $repo
try {
    $revision = git -C .codex/infinigen rev-parse HEAD
    if ($revision -ne 'fb7991e06580639202a4687937082cb63e931eb0' -or (git -C .codex/infinigen diff --name-only HEAD)) {
        throw 'Pinned, unchanged Infinigen source required'
    }
    # Linux Blender has chained .so symlinks that the Windows context sender
    # cannot preserve. Send the contexts with WSL's native Docker client.
    wsl.exe -- docker build --platform linux/amd64 --provenance=false -t soilie-infinigen-timing:pilot `
        --build-context infinigen=.codex/infinigen `
        --build-context dependencies=.codex/infinigen-env/lib/python3.10/site-packages `
        --build-context blender=.codex/tools/blender-3.6.0-linux-x64 `
        --build-context backend=serverless serverless/infinigen_cloud
    if ($LASTEXITCODE -ne 0) { throw 'Compatibility image build failed' }
    $bytes = [long](docker image inspect soilie-infinigen-timing:pilot --format '{{.Size}}')
    if ($bytes -ge 9500000000) { throw 'Image exceeds the 9.5 GB safety gate' }
    # Containerd can report compressed size in .Size. Check the unpacked
    # filesystem too; Lambda enforces an uncompressed image limit.
    $diskUsage = docker run --rm --network none --entrypoint du soilie-infinigen-timing:pilot -sx -B1 /
    if ($LASTEXITCODE -ne 0) { throw 'Could not measure unpacked image' }
    $unpackedBytes = [long](($diskUsage -split '\s+')[0])
    if ($unpackedBytes -ge 9000000000) { throw 'Unpacked filesystem exceeds image safety margin' }
    # No generation or paid call: verify imports in Blender's real interpreter.
    docker run --rm --entrypoint /opt/blender/blender soilie-infinigen-timing:pilot `
        --background --threads 4 --python-use-system-env --python-exit-code 2 `
        --python-expr "import infinigen, gin, scipy; from serverless.benchmark.infinigen_task import controlled_role_counts; print(controlled_role_counts('bedroom', 3))"
    if ($LASTEXITCODE -ne 0) { throw 'Blender import smoke failed' }
    Write-Output "Image ready: $bytes stored bytes; $unpackedBytes unpacked filesystem bytes"
} finally { Pop-Location }
