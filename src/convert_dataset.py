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


def create_association_index(records: list[dict], output_path: str):
    """Создание индекса ассоциаций для быстрого поиска."""
    
    # Группируем ассоциации по данностям
    index = {
        "freedom": {},  # слово -> [id записей]
        "nonsense": {},
        "solitude": {},
        "death": {}
    }
    
    for record in records:
        for givens in ["freedom", "nonsense", "solitude", "death"]:
            for word in record["associations"][givens]:
                if word not in index[givens]:
                    index[givens][word] = []
                index[givens][word].append(record["id"])
    
    # Сохраняем индекс
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    
    print(f"Индекс ассоциаций создан")
    print(f"Уникальных ассоциаций:")
    for givens in index:
        print(f"  {givens}: {len(index[givens])}")
    
    return index


def create_rag_chunks(records: list[dict], output_path: str):
    """Создание чанков для RAG из нарративов."""
    
    chunks = []
    
    for record in records:
        # Чанк о себе
        if record["narratives"]["about_self"]:
            chunks.append({
                "id": f"{record['id']}_self",
                "record_id": record["id"],
                "type": "self_description",
                "demographics": record["demographics"],
                "text": record["narratives"]["about_self"],
                "associations": record["associations"]
            })
        
        # Чанк о первом столкновении со смертью
        if record["narratives"]["first_death"]:
            chunks.append({
                "id": f"{record['id']}_death",
                "record_id": record["id"],
                "type": "death_experience",
                "demographics": record["demographics"],
                "text": record["narratives"]["first_death"],
                "associations": record["associations"]
            })
        
        # Чанк о терапевте
        if record["narratives"]["first_therapist"]:
            chunks.append({
                "id": f"{record['id']}_therapist",
                "record_id": record["id"],
                "type": "therapy_experience",
                "demographics": record["demographics"],
                "text": record["narratives"]["first_therapist"],
                "associations": record["associations"]
            })
        
        # Свободная форма
        if record["narratives"]["free_form"]:
            chunks.append({
                "id": f"{record['id']}_free",
                "record_id": record["id"],
                "type": "free_form",
                "demographics": record["demographics"],
                "text": record["narratives"]["free_form"],
                "associations": record["associations"]
            })
    
    # Сохраняем чанки
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)
    
    print(f"Создано {len(chunks)} RAG-чанков")
    
    return chunks


if __name__ == "__main__":
    # Пути
    input_file = Path(__file__).parent.parent.parent / "AI-Existential Helper (crowd source) (Ответы).xlsx"
    data_dir = Path(__file__).parent.parent / "data"
    data_dir.mkdir(exist_ok=True)
    
    # Конвертируем
    records = convert_dataset(
        str(input_file),
        str(data_dir / "dataset.json")
    )
    
    # Создаём индекс ассоциаций
    create_association_index(
        records,
        str(data_dir / "association_index.json")
    )
    
    # Создаём RAG чанки
    create_rag_chunks(
        records,
        str(data_dir / "rag_chunks.json")
    )