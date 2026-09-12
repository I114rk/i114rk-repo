#!/usr/bin/env python3
import os
import sys
import json
import shutil
import argparse
import subprocess
from pathlib import Path
from datetime import datetime

# ================= НАСТРОЙКИ =================
# Имя вашего репозитория pacman (совпадает с именем базы <REPO_NAME>.db.tar.zst)
REPO_NAME = "i114rk"

# Подпапка с пакетами и базой (если пакеты лежат в подпапке 'x86_64', укажите 'x86_64')
# Если файлы лежат прямо в корне репозитория — оставьте ""
ARCH_SUBDIR = "x86_64"

# Имя JSON-файла для вашей базы данных
DB_JSON_NAME = "packages.json"
# =============================================

ROOT_DIR = Path(__file__).resolve().parent
TARGET_DIR = ROOT_DIR / ARCH_SUBDIR if ARCH_SUBDIR else ROOT_DIR
DB_FILE_NAME = f"{REPO_NAME}.db.tar.zst"
JSON_DB_PATH = ROOT_DIR / DB_JSON_NAME


def run_command(cmd, cwd=None, check=True):
    """Вспомогательная функция для запуска терминальных команд."""
    res = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if check and res.returncode != 0:
        error_output = res.stderr.strip() or res.stdout.strip()
        raise RuntimeError(f"Ошибка выполнения {cmd}:\n{error_output}")
    return res.stdout.strip()


def get_pkg_info(pkg_path: Path):
    """Получает точное имя и версию пакета через pacman -Qp."""
    output = run_command(["pacman", "-Qp", str(pkg_path)])
    parts = output.split()
    if len(parts) >= 2:
        return parts[0], parts[1]
    raise ValueError(f"Не удалось распознать пакет {pkg_path.name}")


def load_packages_db() -> dict:
    """Загружает локальную базу данных JSON."""
    if JSON_DB_PATH.exists():
        try:
            with open(JSON_DB_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_packages_db(db: dict):
    """Сохраняет локальную базу данных JSON с форматированием."""
    with open(JSON_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def git_sync(commit_message: str):
    """Фиксирует изменения и отправляет их на GitHub."""
    print(f"\n[GIT] Синхронизация с GitHub...")
    # Проверяем, есть ли незакоммиченные изменения
    status = run_command(["git", "status", "--porcelain"], cwd=ROOT_DIR)
    if not status:
        print("[GIT] Нет изменений для коммита.")
        return

    run_command(["git", "add", "."], cwd=ROOT_DIR)
    run_command(["git", "commit", "-m", commit_message], cwd=ROOT_DIR)
    print(f"[GIT] Коммит создан: '{commit_message}'")
    
    print("[GIT] Отправка данных (git push)...")
    run_command(["git", "push"], cwd=ROOT_DIR)
    print("[GIT] Успешно загружено на GitHub!")


def scan_and_add():
    """Сканирует каталог на новые/обновленные пакеты."""
    print(f"[*] Сканирование папки: {TARGET_DIR}")
    if not TARGET_DIR.exists():
        TARGET_DIR.mkdir(parents=True, exist_ok=True)

    db_packages = load_packages_db()
    all_pkg_files = list(TARGET_DIR.glob("*.pkg.tar.zst"))

    added_or_updated = []

    for pkg_file in all_pkg_files:
        try:
            pkg_name, pkg_version = get_pkg_info(pkg_file)
        except Exception as e:
            print(f"[!] Ошибка чтения {pkg_file.name}: {e}")
            continue

        existing_entry = db_packages.get(pkg_name)

        # Проверяем, нужно ли добавлять (если пакета нет в JSON или изменилась версия/файл)
        is_new = existing_entry is None
        is_updated = existing_entry and (
            existing_entry.get("version") != pkg_version or 
            existing_entry.get("filename") != pkg_file.name
        )

        if is_new or is_updated:
            action = "Новый пакет" if is_new else "Обновление версии"
            print(f"[+] {action}: {pkg_name} ({pkg_version}) -> {pkg_file.name}")
            
            # Добавляем в базу репозитория через repo-add
            db_path = TARGET_DIR / DB_FILE_NAME
            run_command(["repo-add", str(db_path), str(pkg_file)], cwd=TARGET_DIR)

            # Обновляем запись в JSON
            db_packages[pkg_name] = {
                "name": pkg_name,
                "version": pkg_version,
                "filename": pkg_file.name,
                "size_bytes": pkg_file.stat().st_size,
                "updated_at": datetime.now().isoformat()
            }
            added_or_updated.append(f"{pkg_name} {pkg_version}")

    if added_or_updated:
        save_packages_db(db_packages)
        commit_msg = f"Auto-update repo: added/updated {', '.join(added_or_updated)}"
        git_sync(commit_msg)
    else:
        print("[*] Все пакеты уже добавлены и актуальны. Никаких изменений.")


def remove_package(package_name: str):
    """Удаляет пакет из repo, диска, JSON базы и пушит в Git."""
    print(f"[*] Запрос на удаление пакета: '{package_name}'")
    db_packages = load_packages_db()

    # 1. Удаление из базы данных pacman через repo-remove
    db_path = TARGET_DIR / DB_FILE_NAME
    if db_path.exists():
        print(f"[+] Удаление из базы repo-remove: {package_name}")
        # repo-remove вернет ненулевой код, если пакета не было в базе, поэтому check=False
        run_command(["repo-remove", str(db_path), package_name], cwd=TARGET_DIR, check=False)

    # 2. Удаление физических файлов (*.pkg.tar.zst, *.sig)
    deleted_files = 0
    for f in TARGET_DIR.glob(f"{package_name}-*.pkg.tar.zst*"):
        try:
            f.unlink()
            print(f"[+] Удален файл: {f.name}")
            deleted_files += 1
        except Exception as e:
            print(f"[!] Не удалось удалить файл {f.name}: {e}")

    # 3. Удаление из JSON базы
    if package_name in db_packages:
        del db_packages[package_name]
        save_packages_db(db_packages)
        print(f"[+] Пакет '{package_name}' удален из {DB_JSON_NAME}")
    else:
        print(f"[?] Пакет '{package_name}' отсутствовал в {DB_JSON_NAME}")

    # 4. Git Push
    commit_msg = f"Remove package: {package_name}"
    git_sync(commit_msg)
    print(f"\n[✓] Пакет '{package_name}' успешно удален и синхронизирован!")


def main():
    parser = argparse.ArgumentParser(description="Автоматический менеджер репозитория pacman для GitHub")
    # Поддерживаем и -remove, и --remove, и -r
    parser.add_argument(
        "-remove", "--remove", "-r",
        dest="remove_name",
        nargs="+",
        help="Имя пакета для удаления из репозитория"
    )

    args = parser.parse_args()

    if args.remove_name:
        pkg_to_remove = " ".join(args.remove_name).strip()
        remove_package(pkg_to_remove)
    else:
        scan_and_add()


if __name__ == "__main__":
    main()
