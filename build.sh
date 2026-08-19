#!/usr/bin/env bash
set -e

echo "📦 Python bağımlılıkları kuruluyor..."
pip install -r requirements.txt

echo "🔎 Node.js sürümü kontrol ediliyor..."
node --version || echo "⚠️ Node bulunamadı! Render Environment'a NODE_VERSION=20 ekleyin."

echo "📦 PO Token provider (bgutil-ytdlp-pot-provider) kuruluyor..."
BGUTIL_DIR="$HOME/bgutil-ytdlp-pot-provider"
if [ ! -d "$BGUTIL_DIR" ]; then
    git clone --single-branch --branch 1.3.1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git "$BGUTIL_DIR"
fi
cd "$BGUTIL_DIR/server"
npm ci
npx tsc
cd -

echo "✅ Build tamamlandı."
