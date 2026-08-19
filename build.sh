#!/usr/bin/env bash
set -e

echo "📦 Python bağımlılıkları kuruluyor..."
pip install -r requirements.txt

echo "🔎 Node.js sürümü kontrol ediliyor..."
node --version || echo "⚠️ Node bulunamadı! Render Environment'a NODE_VERSION=20 ekleyin."

echo "📦 PO Token provider (bgutil-ytdlp-pot-provider) kuruluyor..."
# Proje kök dizinine (repo'nun içine) kuruyoruz, böylece build ve runtime aynı yolu görür.
BGUTIL_DIR="$(pwd)/bgutil-ytdlp-pot-provider"
# Her build'de temiz kurulum yapıyoruz; eksik/bozuk package-lock.json gibi
# önceki build kalıntılarının npm ci'yi bozmasını önlemek için.
rm -rf "$BGUTIL_DIR"
git clone --single-branch --branch 1.3.1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git "$BGUTIL_DIR"
cd "$BGUTIL_DIR/server"
npm ci
npx tsc
cd -

echo "✅ Build tamamlandı. BGUTIL_DIR=$BGUTIL_DIR"
