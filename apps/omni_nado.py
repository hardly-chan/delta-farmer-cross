import asyncio

from clients.nado import NadoClient
from clients.omni import OmniClient
from lib.cli import create_cli, run_app
from strategy.spread_cycle import SpreadStrategy
from strategy.spread_models import SpreadConfig
from strategy.runner import close_all, print_positions


async def print_info(omni: OmniClient, nado: NadoClient) -> None:
    await asyncio.gather(omni.warmup(), nado.warmup())
    omni_profile, nado_profile = await asyncio.gather(omni.profile(), nado.profile())

    print(f"Omni {omni.name}: balance={omni_profile.balance:.2f} volume={omni_profile.volume:.2f}")
    print(f"Nado {nado.name}: balance={nado_profile.balance:.2f} volume={nado_profile.volume:.2f}")


async def main() -> None:
    cli = await create_cli("omni_nado", "configs/omni_nado.toml", ["privkey"])
    cfg = SpreadConfig.load(cli.config)

    omni = OmniClient.from_config(cfg.omni)
    nado = NadoClient.from_config(cfg.nado)
    active = [client for client, enabled in ((omni, cfg.omni.enabled), (nado, cfg.nado.enabled)) if enabled]

    match cli.command:
        case "trade":
            if len(active) != 2:
                raise SystemExit("Both omni and nado accounts must be enabled for spread trading")
            strategy = SpreadStrategy(cfg, omni=omni, nado=nado)
            await strategy.run()
        case "close":
            await close_all(active)
        case "positions":
            await print_positions(active)
        case "info":
            await print_info(omni, nado)
        case "stats":
            print("Stats command is not supported for omni_nado spread mode")


if __name__ == "__main__":
    run_app(main())
