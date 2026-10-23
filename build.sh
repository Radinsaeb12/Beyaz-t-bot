#!/usr/bin/env bash
set -e

echo "📦 Python bağımlılıkları yükleniyor..."
pip install --upgrade pip
pip install -r requirements.txt

echo "🎬 FFmpeg ikili dosyaları hazırlanıyor..."
python3 -c "import static_ffmpeg; static_ffmpeg.add_paths()"

echo "✅ Build başarıyla tamamlandı!"
