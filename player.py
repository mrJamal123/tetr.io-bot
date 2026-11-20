"""Entry point for the TETR.IO bot; runtime options live in jogar.py."""

import traceback

import jogar


if __name__ == "__main__":
    try:
        jogar.main()
    except KeyboardInterrupt:
        print("\nBot interrompido pelo usuario.")
    except Exception:
        traceback.print_exc()
    finally:
        try:
            input("\nPressione Enter para fechar...")
        except EOFError:
            pass
