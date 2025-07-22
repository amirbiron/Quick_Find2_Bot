#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import aiosqlite
import logging
from typing import List, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# שם קובץ מסד הנתונים
DB_FILE = "guides.db"


async def init_db():
    """אתחול מסד הנתונים ויצירת הטבלאות"""
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS guides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER UNIQUE NOT NULL,
                title TEXT NOT NULL,
                date_created TIMESTAMP NOT NULL,
                date_saved TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # יצירת אינדקס לחיפוש מהיר
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_message_id ON guides(message_id)
        ''')
        
        await db.commit()
        logger.info("מסד הנתונים אותחל בהצלחה")


async def save_guide(message_id: int, title: str, date_created: datetime) -> bool:
    """שמירת מדריך חדש במסד הנתונים"""
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute('''
                INSERT OR REPLACE INTO guides (message_id, title, date_created)
                VALUES (?, ?, ?)
            ''', (message_id, title, date_created.isoformat()))
            
            await db.commit()
            return True
            
    except Exception as e:
        logger.error(f"שגיאה בשמירת מדריך {message_id}: {e}")
        return False


async def get_all_guides(limit: Optional[int] = None) -> List[Dict]:
    """קבלת כל המדריכים ממסד הנתונים"""
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            # מיון לפי תאריך יצירה (החדשים ראשון)
            query = '''
                SELECT message_id, title, date_created, date_saved
                FROM guides
                ORDER BY date_created DESC
            '''
            
            if limit:
                query += f' LIMIT {limit}'
            
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            
            guides = []
            for row in rows:
                guides.append({
                    'message_id': row[0],
                    'title': row[1],
                    'date_created': row[2],
                    'date_saved': row[3]
                })
            
            return guides
            
    except Exception as e:
        logger.error(f"שגיאה בקבלת מדריכים: {e}")
        return []


async def get_guides_count() -> int:
    """קבלת מספר המדריכים הכולל"""
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute('SELECT COUNT(*) FROM guides')
            result = await cursor.fetchone()
            return result[0] if result else 0
            
    except Exception as e:
        logger.error(f"שגיאה בספירת מדריכים: {e}")
        return 0


async def get_guide_by_message_id(message_id: int) -> Optional[Dict]:
    """קבלת מדריך ספציפי לפי message_id"""
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute('''
                SELECT message_id, title, date_created, date_saved
                FROM guides
                WHERE message_id = ?
            ''', (message_id,))
            
            row = await cursor.fetchone()
            
            if row:
                return {
                    'message_id': row[0],
                    'title': row[1],
                    'date_created': row[2],
                    'date_saved': row[3]
                }
            
            return None
            
    except Exception as e:
        logger.error(f"שגיאה בחיפוש מדריך {message_id}: {e}")
        return None


async def delete_guide(message_id: int) -> bool:
    """מחיקת מדריך מהמסד"""
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute('DELETE FROM guides WHERE message_id = ?', (message_id,))
            await db.commit()
            
            return cursor.rowcount > 0
            
    except Exception as e:
        logger.error(f"שגיאה במחיקת מדריך {message_id}: {e}")
        return False


async def search_guides(search_term: str, limit: int = 10) -> List[Dict]:
    """חיפוש מדריכים לפי מילות מפתח"""
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute('''
                SELECT message_id, title, date_created, date_saved
                FROM guides
                WHERE title LIKE ?
                ORDER BY date_created DESC
                LIMIT ?
            ''', (f'%{search_term}%', limit))
            
            rows = await cursor.fetchall()
            
            guides = []
            for row in rows:
                guides.append({
                    'message_id': row[0],
                    'title': row[1],
                    'date_created': row[2],
                    'date_saved': row[3]
                })
            
            return guides
            
    except Exception as e:
        logger.error(f"שגיאה בחיפוש מדריכים '{search_term}': {e}")
        return []


async def get_recent_guides(days: int = 7, limit: int = 10) -> List[Dict]:
    """קבלת המדריכים האחרונים מתקופה מסוימת"""
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute('''
                SELECT message_id, title, date_created, date_saved
                FROM guides
                WHERE date_created >= datetime('now', '-{} days')
                ORDER BY date_created DESC
                LIMIT ?
            '''.format(days), (limit,))
            
            rows = await cursor.fetchall()
            
            guides = []
            for row in rows:
                guides.append({
                    'message_id': row[0],
                    'title': row[1],
                    'date_created': row[2],
                    'date_saved': row[3]
                })
            
            return guides
            
    except Exception as e:
        logger.error(f"שגיאה בקבלת מדריכים אחרונים: {e}")
        return []


async def export_guides() -> List[Dict]:
    """ייצוא כל המדריכים לגיבוי"""
    return await get_all_guides()


async def import_guides(guides_data: List[Dict]) -> int:
    """ייבוא מדריכים מגיבוי"""
    imported_count = 0
    
    for guide in guides_data:
        try:
            if await save_guide(
                message_id=guide['message_id'],
                title=guide['title'],
                date_created=datetime.fromisoformat(guide['date_created'])
            ):
                imported_count += 1
        except Exception as e:
            logger.error(f"שגיאה בייבוא מדריך {guide.get('message_id', 'לא ידוע')}: {e}")
    
    return imported_count


async def get_database_stats() -> Dict:
    """סטטיסטיקות מסד הנתונים"""
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            # סה"כ מדריכים
            cursor = await db.execute('SELECT COUNT(*) FROM guides')
            total_guides = (await cursor.fetchone())[0]
            
            # המדריך הראשון והאחרון
            cursor = await db.execute('SELECT MIN(date_created), MAX(date_created) FROM guides')
            date_range = await cursor.fetchone()
            
            # מדריכים מהשבוע האחרון
            cursor = await db.execute('''
                SELECT COUNT(*) FROM guides 
                WHERE date_created >= datetime('now', '-7 days')
            ''')
            weekly_guides = (await cursor.fetchone())[0]
            
            return {
                'total_guides': total_guides,
                'first_guide_date': date_range[0],
                'latest_guide_date': date_range[1],
                'weekly_guides': weekly_guides
            }
            
    except Exception as e:
        logger.error(f"שגיאה בקבלת סטטיסטיקות: {e}")
        return {
            'total_guides': 0,
            'first_guide_date': None,
            'latest_guide_date': None,
            'weekly_guides': 0
        }
