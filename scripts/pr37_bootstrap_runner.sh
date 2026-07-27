#!/usr/bin/env bash
set -Eeuo pipefail

if grep -q 'version = "0.14.0"' pyproject.toml; then
  echo "changed=false" >> "$GITHUB_OUTPUT"
  exit 0
fi

echo '== verify payload =='
echo '4235ec6f747a13eff00bc4e60681f343b90ec9a00848dc5a4154b2cf37512e00  scripts/pr37_bootstrap.part.00' | sha256sum -c -
echo 'a3d9782be85163db440b2c54e9a2e2f3b06771e18ad2671319006dcddcaf9989  scripts/pr37_bootstrap.part.01' | sha256sum -c -
echo 'eeeeb31c90f1fd74c4cb1d0f78f57cac11a6bc417c3bbb2c0332f4f0fd1b02e3  scripts/pr37_bootstrap.part.02' | sha256sum -c -
echo '63eaab04d7d9bd3a5caffa968e254a624dad3ea18cd2c7d5ed37a3b3605ff766  scripts/pr37_bootstrap.part.03' | sha256sum -c -
echo '4b2c54a653817e5223d59d521b4f4c225de7f1fa3032451d4033eddca49c88c7  scripts/pr37_bootstrap.part.04a' | sha256sum -c -
echo 'a7f0967fc1fe3364dd299aa4c74c7ad31dde2e15a4432d3e5750105da82be2b1  scripts/pr37_bootstrap.part.04b' | sha256sum -c -
echo '66f13ae29722d62ea0047d25178d4878f8e68a211947436a538ed310ccef9e23  scripts/pr37_bootstrap.part.04c' | sha256sum -c -
echo '32882fa82aef260520b29c488d0e4feab65f54bac999b68d7b848261b0577e4a  scripts/pr37_bootstrap.part.05' | sha256sum -c -

cat \
  scripts/pr37_bootstrap.part.00 \
  scripts/pr37_bootstrap.part.01 \
  scripts/pr37_bootstrap.part.02 \
  scripts/pr37_bootstrap.part.03 \
  scripts/pr37_bootstrap.part.04a \
  scripts/pr37_bootstrap.part.04b \
  scripts/pr37_bootstrap.part.04c \
  scripts/pr37_bootstrap.part.05 \
  | tr -d '\r\n' > scripts/pr37_bootstrap.py.gz.b64.assembled

echo '4284f0f7c91b6ca122214f85d5ddcf7974d306b1c62492590f1c5ffcc571cfae  scripts/pr37_bootstrap.py.gz.b64.assembled' | sha256sum -c -
base64 -d scripts/pr37_bootstrap.py.gz.b64.assembled | gzip -d > scripts/pr37_bootstrap.py
echo 'ecb5540213aa15a27172342658d567cbdab9769f0e672f3c26e168cdce57a80b  scripts/pr37_bootstrap.py' | sha256sum -c -

echo '== apply =='
python scripts/pr37_bootstrap.py
python - <<'PY'
from pathlib import Path
path = Path('src/tutor_assistant/ui/app.py')
text = path.read_text(encoding='utf-8')
old = 'str(error) + "\n\nПовторить только неопределённые блоки?"'
new = 'str(error) + "\\n\\nПовторить только неопределённые блоки?"'
if old not in text:
    raise SystemExit('Generated Yandex confirmation marker not found')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
PY

echo '== lock =='
python -m pip install --quiet uv
uv lock

echo '== compile =='
python -m compileall -q src/tutor_assistant tests

git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com
git add README.md docs pyproject.toml uv.lock src tests
git commit -m "Add durable resumable normalization checkpoints"
git push --quiet origin "HEAD:${HEAD_BRANCH}"
echo "changed=true" >> "$GITHUB_OUTPUT"
