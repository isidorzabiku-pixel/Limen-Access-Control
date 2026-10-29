# Contributing to Limen

Thanks for your interest! Limen is licensed under the **GNU AGPL-3.0** (see `LICENSE`) and is also offered under a
separate commercial license by its maintainer (see `README.md`).

## Before you open a pull request
1. Read `docs/ARCHITECTURE.md` and `docs/DEVELOPMENT.md` (workflow and checks), and keep to the design principles below.
2. Branch from `dev` and open the pull request against `dev`.
3. Any visible text goes through `t()` / `T()` with its English translation in `backend/lingue/en.json`; run
   `python3 backend/tools/controlla_traduzioni.py` and `python3 backend/tools/test_lingua.py`.
4. **Agree to the Contributor License Agreement** (`CLA.md`) by writing "I have read and agree to the CLA" in the pull
   request description. Pull requests without it cannot be merged.

## Security issues
Do not open a public issue. Contact the maintainer privately.

## Design principles
- Gates always decide locally from their cache; remote opening is a command from the master, never a request to the gate.
- Fixed IP addresses, no DHCP: a device's IP is entered only on the master, which contacts the device first. The device never writes first and never broadcasts.
- The web app loads nothing from the internet; the device interface is English only.
- Explanations in the interface live behind the ⓘ icon: one short sentence, no scattered small text.
- Passages in Monitoring and Log are never deleted by configuration changes; rules and groups are never deleted automatically (they become "incomplete").
