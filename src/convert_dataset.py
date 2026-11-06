"""
Конвертация датасета из xlsx в структурированный JSON для RAG.
"""

import json
import pandas as pd
from pathlib import Path


def parse_associations(assoc_str: str) -> list[str]:
    """Парсинг строки ассоциаций в список слов."""
    if pd.isna(assoc_str) or not assoc_str.strip():
        return []
    
    # Разделители: запятая, пробел, точка с запятой
    associations = []
    for sep in [',', ';', ' ']:
        if sep in assoc_str:
            associations = [a.strip().lower() for a in assoc_str.split(sep) if a.strip()]
            break
    
    if not associations:
        associations = [assoc_str.strip().lower()]
    
    return associations


def convert_dataset(input_path: str, output_path: str):
    """Конвертация xlsx в JSON."""
    
    # Читаем xlsx
    df = pd.read_excel(input_path)
    
    # Нормализуем названия колонок
    df.columns = [str(c).strip() for c in df.columns]
    
    records = []
    
    for idx, row in df.iterrows():
        record = {
            "id": idx + 1,
            "timestamp": str(row.get("Отметка времени", "")),
            
            # Ассоциации к четырём данностям
            "associations": {
                "freedom": parse_associations(row.get("Подберите 5 слов-ассоциаций к слову свобода (желательно через запятую с маленькой буквы):", "")),
                "nonsense": parse_associations(row.get("Подберите 5 слов-ассоциаций к слову бессмысленность:", "")),
                "solitude": parse_associations(row.get("Подберите 5 слов-ассоциаций к слову одиночество:", "")),
                "death": parse_associations(row.get("Подберите 5 слов-ассоциаций к слову смерть:", ""))
            },
            
            # Свободные ответы
            "narratives": {
                "about_self": str(row.get("Расскажите о себе (в свободной форме, можно без конкретики):", "")).strip() if pd.notna(row.get("Расскажите о себе (в свободной форме, можно без конкретики):", "")) else "",
                "school_story": str(row.get("Расскажите забавную историю из ваших школьных времён:", "")).strip() if pd.notna(row.get("Расскажите забавную историю из ваших школьных времён:", "")) else "",
                "first_therapist": str(row.get("Расскажите о вашем первом психологе (если был таковой):", "")).strip() if pd.notna(row.get("Расскажите о вашем первом психологе (если был таковой):", "")) else "",
                "first_death": str(row.get("Помните ли вы, когда впервые столкнулись со смертью? Расскажите:", "")).strip() if pd.notna(row.get("Помните ли вы, когда впервые столкнулись со смертью? Расскажите:", "")) else "",
                "free_form": str(row.get("Здесь можно написать что угодно в свободной форме (желательно связный текст):", "")).strip() if pd.notna(row.get("Здесь можно написать что угодно в свободной форме (желательно связный текст):", "")) else ""
            },
            
            # Демография
            "demographics": {
                "is_lonely": str(row.get("Вы одиноки? ", "")).strip() if pd.notna(row.get("Вы одиноки? ", "")) else "",
                "is_happy": str(row.get("Вы скорее счастливы?", "")).strip() if pd.notna(row.get("Вы скорее счастливы?", "")) else "",
                "age": str(row.get("Напишите свой возраст (цифрами):", "")).strip() if pd.notna(row.get("Напишите свой возраст (цифрами):", "")) else "",
                "gender": str(row.get("Выберите пол:", "")).strip() if pd.notna(row.get("Выберите пол:", "")) else ""
            }
        }
        
        # Добавляем только если есть хотя бы одна ассоциация
        if any(record["associations"].values()):
            records.append(record)
    
    # Сохраняем JSON
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    
    print(f"Конвертировано {len(records)} записей")
    print(f"Сохранено в: {output_path}")
    
    return records


if __name__ == "__main__":
    import argparse
    from crowd_artifacts import build_artifacts

    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Convert an XLSX and build the canonical crowd artifacts")
    parser.add_argument("--input", type=Path, default=root.parent / "AI-Existential Helper (crowd source) (Ответы).xlsx")
    parser.add_argument("--data-dir", type=Path, default=root / "data")
    parser.add_argument("--force", action="store_true", help="Allow replacing existing canonical artifacts")
    args = parser.parse_args()
    names = ("dataset.json", "association_index.json", "association_items.json", "narrative_chunks.json")
    existing = [name for name in names if (args.data_dir / name).exists()]
    if existing and not args.force:
        parser.error(f"Refusing to overwrite existing files: {', '.join(existing)}; use --force")
    if not args.input.is_file():
        parser.error(f"Input file does not exist: {args.input}")
    args.data_dir.mkdir(parents=True, exist_ok=True)
    dataset = args.data_dir / "dataset.json"
    convert_dataset(str(args.input), str(dataset))
    build_artifacts(dataset, args.data_dir)
