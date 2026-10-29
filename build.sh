#!/usr/bin/env bash
# Hata durumunda betiği durdur
set -o errexit

# Pip'i güncelle ve kütüphaneleri yükle
python -m pip install --upgrade pip
pip install -r requirements.txt