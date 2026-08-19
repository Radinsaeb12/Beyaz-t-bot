#!/usr/bin/env bash
set -e

echo "📦 Deno kuruluyor (JS Challenge Solver için)..."
curl -fsSL https://deno.land/install.sh | sh
export DENO_INSTALL="$HOME/.deno"
export PATH="$DENO_INSTALL/bin:$PATH"

echo "📦 Python bağımlılıkları kuruluyor..."
pip install -r requirements.txt

echo "🔎 Node.js ve Deno kontrol ediliyor..."
node --version || echo "⚠️ Node bulunamadı!"
deno --version || echo "⚠️ Deno bulunamadı!"

echo "📦 PO Token provider (bgutil-ytdlp-pot-provider) kuruluyor..."
BGUTIL_DIR="$(pwd)/bgutil-ytdlp-pot-provider"
rm -rf "$BGUTIL_DIR"
git clone --single-branch --branch 1.3.1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git "$BGUTIL_DIR"
cd "$BGUTIL_DIR/server"
npm ci
npx tsc
cd -

echo "✅ Build tamamlandı. BGUTIL_DIR=$BGUTIL_DIR"