from algopy import ARC4Contract, Account, Asset, Global, String, Txn, UInt64, gtxn, itxn, op, subroutine
from algopy.arc4 import Bool, abimethod

# Route Contract - Factory Contract - *Tokens Contract*

TOTAL_SUPPLY = 10_000_000_000
SCALE = 1000
FEE = 5
FACTOR = SCALE - FEE

class AmmDex(ARC4Contract):
    def __init__(self) -> None:
        self.asset_a = Asset();
        self.asset_b = Asset();
        self.governor = Txn.sender; # admin
        self.pool_token = Asset();
        self.ratio = UInt64(0);

    @abimethod
    def bootstrap(self, seed: gtxn.PaymentTransaction, a_asset: Asset, b_asset: Asset) -> UInt64:
        # self._check_governor();

        # assert

        self.asset_a = a_asset;
        self.asset_b = b_asset;
        self.pool_token = self._create_pool_token();

        # opt in -> asset

        self._do_opt_in(a_asset);
        self._do_opt_in(b_asset);
        return self.pool_token.id

    @subroutine
    def _check_governor(self) -> None:
        assert(Txn.sender == self.governor), "Only the governor may call this method"

    @subroutine
    def _create_pool_token(self) -> Asset:
        return (
            itxn.AssetConfig(
                asset_name=b"AMMDEX-" + self.asset_a.unit_name + self.asset_b.unit_name, # unit token name id
                unit_name=b"ammdex",
                total=TOTAL_SUPPLY,
                decimals=3,
                manager=Global.current_application_address,
                reserve=Global.current_application_address,
            )
            .submit()
            .created_asset
        )

    @subroutine
    def _do_opt_in(self, asset: Asset) -> None:
        do_asset_transfer(
            receiver=Global.current_application_address,
            asset=asset,
            amount=UInt64(0)
        )


@subroutine
def do_asset_transfer(*, receiver: Account, asset: Asset, amount: UInt64) -> None:
    itxn.AssetTransfer(
        xfer_asset=asset,
        asset_amount=amount,
        asset_receiver=receiver
    ).submit()

@subroutine
def tokens_to_mint(
        *,
        pool_balance: UInt64,
        a_balance: UInt64,
        b_balance: UInt64,
        a_amount: UInt64,
        b_amount: UInt64
) -> UInt64:
    is_initial_mint = a_balance == a_amount and b_balance == b_amount
    if is_initial_mint:
        return op.sqrt(a_amount * b_amount) - SCALE

    issued = TOTAL_SUPPLY - pool_balance
    a_ratio = SCALE * a_amount // (a_balance - a_amount)
    b_ratio = SCALE * b_amount // (b_balance - b_amount)
    if a_ratio < b_ratio:
        return a_ratio * issued // SCALE
    else:
        return b_ratio * issued // SCALE

@subroutine
def tokens_to_burn(*, pool_balance: UInt64, supply: UInt64, amount: UInt64) -> UInt64:
    issued = TOTAL_SUPPLY - pool_balance - amount
    return supply * amount // issued

@subroutine
def tokens_to_swap(*, in_amount: UInt64, in_supply: UInt64, out_supply: UInt64) -> UInt64:
    in_total = SCALE * (in_supply - in_amount) + (in_amount * FACTOR)
    out_total = in_amount * FACTOR * out_supply
    return out_total // in_total
