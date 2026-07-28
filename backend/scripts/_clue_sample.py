import asyncio
from sqlalchemy import func, select
from app.core.database import async_session_factory
from app.models.clue import ClueActivePointer, MachineClue


async def main() -> None:
    async with async_session_factory() as s:
        n = await s.scalar(
            select(func.count()).select_from(MachineClue).where(MachineClue.novel_id == 91)
        )
        print("machine_clues", n)
        ptr = await s.scalar(
            select(ClueActivePointer).where(ClueActivePointer.novel_id == 91)
        )
        print("active_version", ptr.version_id if ptr else None)
        samples = (
            await s.scalars(
                select(MachineClue)
                .where(MachineClue.novel_id == 91)
                .order_by(MachineClue.id.asc())
                .limit(5)
            )
        ).all()
        for c in samples:
            print(
                c.id,
                float(c.confidence),
                c.publication_status,
                "cue_ch",
                c.first_cue_chapter,
                c.title[:70],
            )


if __name__ == "__main__":
    asyncio.run(main())
