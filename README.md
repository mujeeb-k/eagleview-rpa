# Eagleview

Work in progress. See PLAN.md for the build plan.

## Mock bank

`make bank` starts "Ironbridge Teller 7", the fictional legacy teller app Eagleview automates, as two
tenants: Lantern Bay FCU on http://127.0.0.1:8601 (operator `teller01`) and Copperhill CCU on
http://127.0.0.1:8602 (operator `tlr2210`). Each tenant has its own members and its own password.
On first run, `make bank` generates both passwords into `.env` (`LANTERN_BAY_TELLER_PASSWORD`,
`COPPERHILL_TELLER_PASSWORD`), so there is nothing to set up.

All institutions, people, and data in this repository are fictional and synthetic.
