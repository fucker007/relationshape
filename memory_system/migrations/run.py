#!/usr/bin/env python3
"""运行数据库迁移"""
import asyncio
import asyncpg
import os
from pathlib import Path

async def run_migrations():
    """执行所有迁移文件"""
    db_url = os.getenv("DATABASE_URL", "postgresql://memory:memory@localhost:5433/memory")
    conn = await asyncpg.connect(db_url)

    migrations_dir = Path(__file__).parent
    migration_files = sorted(migrations_dir.glob("*.sql"))

    for migration_file in migration_files:
        print(f"Running {migration_file.name}...")
        with open(migration_file) as f:
            sql = f.read()
        try:
            await conn.execute(sql)
            print(f"✓ {migration_file.name} completed")
        except Exception as e:
            print(f"✗ {migration_file.name} failed: {e}")

    await conn.close()

if __name__ == "__main__":
    asyncio.run(run_migrations())
