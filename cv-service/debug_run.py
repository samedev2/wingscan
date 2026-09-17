"""Debug direto: inicializa app via lifespan e roda 5s."""
import sys
import os
import asyncio
import traceback
os.environ['CV_VIDEO_PATH'] = r'C:\Users\Administrador\Downloads\PROJETOS CONSELHO TECH\wingscam\cv-service\data\uploads\1789564122_8940b26f.mp4'
os.environ['CV_VIDEO_LOOP'] = '1'
sys.path.insert(0, r'C:\Users\Administrador\Downloads\PROJETOS CONSELHO TECH\wingscam\cv-service')
import app

async def main():
    async with app.lifespan(app.app):
        print("[boot] running for 5s...", flush=True)
        await asyncio.sleep(5)
        print(f"[boot] latest_analytics = {app.state.latest_analytics}", flush=True)
        print(f"[boot] identified = {list(app.state.identified_birds.values())[:5]}", flush=True)

try:
    asyncio.run(main())
except Exception as e:
    print(f"FATAL: {type(e).__name__}: {e}")
    traceback.print_exc()
