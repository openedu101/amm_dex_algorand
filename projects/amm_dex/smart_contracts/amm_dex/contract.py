from algopy import Account, ARC4Contract, Asset, Global, Txn, UInt64, arc4, gtxn, itxn, op, subroutine

# Total supply of the pool tokens
TOTAL_SUPPLY = 10_000_000_000
# scale helps with precision when doing computation for
# the number of tokens to transfer
SCALE = 1000
# Fee for swaps, 5 represents 0.5% ((fee / scale)*100)
FEE = 5
FACTOR = SCALE - FEE


class ConstantProductAMM(ARC4Contract):
    def __init__(self) -> None:
        # init chạy bất cứ khi nào ID ứng dụng của giao dịch bằng không, và chạy đầu tiên

        # ID tài sản của tài sản A
        self.asset_a = Asset()
        # ID tài sản của tài sản B
        self.asset_b = Asset()
        # Quản trị viên hiện tại của hợp đồng này, được phép thực hiện các hành động admin
        self.governor = Txn.sender
        # ID asset của Token Pool, được sử dụng để theo dõi phần shared của pool mà người nắm giữ có thể withdraw
        self.pool_token = Asset()
        # Tỷ lệ giữa các tài sản (A*Scale/B)
        self.ratio = UInt64(0)

    @arc4.abimethod()
    def set_governor(self, new_governor: Account) -> None:
        """thiết lập quản trị viên của contract, chỉ có thể được gọi bởi quản trị viên hiện tại"""
        self._check_is_governor()
        self.governor = new_governor

    @arc4.abimethod()
    def bootstrap(self, seed: gtxn.PaymentTransaction, a_asset: Asset, b_asset: Asset) -> UInt64:
        """
        Hàm này thiết lập ban đầu cho hợp đồng bằng cách:
            1. Đăng ký (opt in) vào các tài sản
            2. Tạo ra pool token

        Lưu ý quan trọng:
            - Hàm này chỉ có thể chạy một lần duy nhất cho mỗi hợp đồng.
            - Nếu chạy lần thứ hai, nó sẽ thất bại vì các giá trị của asset và pool token được đặt là không thay đổi (static) sau khi đã thiết lập.

        Các thông số đầu vào:
            seed: Một khoản tiền ban đầu gửi vào tài khoản của ứng dụng.
            Khoản tiền này giúp ứng dụng có thể đăng ký vào các asset và tạo pool token.
            a_asset: Một trong hai loại tài sản mà pool này sẽ cho phép swap.
            b_asset: Loại tài sản còn lại mà pool này sẽ cho phép swap.

        Kết quả trả về:
            ID của pool token mới được tạo ra.
        """
        assert not self.pool_token, "application has already been bootstrapped"
        self._check_is_governor()
        assert Global.group_size == 2, "group size not 2"
        assert seed.receiver == Global.current_application_address, "receiver not app address"

        assert seed.amount >= 300_000, "amount minimum not met"  # 0.3 Algos
        assert a_asset.id < b_asset.id, "asset a must be less than asset b"
        self.asset_a = a_asset
        self.asset_b = b_asset
        self.pool_token = self._create_pool_token()

        self._do_opt_in(self.asset_a)
        self._do_opt_in(self.asset_b)
        return self.pool_token.id

    @arc4.abimethod(
        default_args={
            "pool_asset": "pool_token",
            "a_asset": "asset_a",
            "b_asset": "asset_b",
        },
    )
    def mint(
        self,
        a_xfer: gtxn.AssetTransferTransaction,
        b_xfer: gtxn.AssetTransferTransaction,
        pool_asset: Asset,
        a_asset: Asset,
        b_asset: Asset,
    ) -> None:
        """
        Hàm này tạo ra (mint) pool token dựa trên số lượng asset A và asset B được cung cấp.

        Cách hoạt động:
        1. Nhận một lượng asset A và asset B thông qua các giao dịch chuyển khoản.
        2. Tạo ra một số lượng pool token tương ứng, dựa trên:
           - Số dư hiện tại của pool
           - Số lượng pool token đang lưu hành

        Các thông số đầu vào:
            a_xfer: Giao dịch chuyển khoản asset A vào pool.
                    Đây là khoản deposit để đổi lấy pool token.
            b_xfer: Giao dịch chuyển khoản asset B vào pool.
                    Đây cũng là khoản deposit để đổi lấy pool token.
            pool_asset: ID của pool token, để chúng ta có thể phân phối nó.
            a_asset: ID của asset A, để chúng ta có thể kiểm tra số dư.
            b_asset: ID của asset B, để chúng ta có thể kiểm tra số dư.
        """
        self._check_bootstrapped()

        # well-formed mint
        assert pool_asset == self.pool_token, "asset pool incorrect"
        assert a_asset == self.asset_a, "asset a incorrect"
        assert b_asset == self.asset_b, "asset b incorrect"
        assert a_xfer.sender == Txn.sender, "sender invalid"
        assert b_xfer.sender == Txn.sender, "sender invalid"

        # valid asset a xfer
        assert (
            a_xfer.asset_receiver == Global.current_application_address
        ), "receiver not app address"
        assert a_xfer.xfer_asset == self.asset_a, "asset a incorrect"
        assert a_xfer.asset_amount > 0, "amount minimum not met"

        # valid asset b xfer
        assert (
            b_xfer.asset_receiver == Global.current_application_address
        ), "receiver not app address"
        assert b_xfer.xfer_asset == self.asset_b, "asset b incorrect"
        assert b_xfer.asset_amount > 0, "amount minimum not met"

        to_mint = tokens_to_mint(
            pool_balance=self._current_pool_balance(),
            a_balance=self._current_a_balance(),
            b_balance=self._current_b_balance(),
            a_amount=a_xfer.asset_amount,
            b_amount=b_xfer.asset_amount,
        )
        assert to_mint > 0, "send amount too low"

        # mint tokens
        do_asset_transfer(receiver=Txn.sender, asset=self.pool_token, amount=to_mint)
        self._update_ratio()

    @arc4.abimethod(
        default_args={
            "pool_asset": "pool_token",
            "a_asset": "asset_a",
            "b_asset": "asset_b",
        },
    )
    def burn(
        self,
        pool_xfer: gtxn.AssetTransferTransaction,
        pool_asset: Asset,
        a_asset: Asset,
        b_asset: Asset,
    ) -> None:
        """
        Hàm này đốt (burn) pool token để nhận lại một lượng asset A và asset B tương ứng.

        Cách hoạt động:
        1. Người dùng gửi pool token vào hợp đồng.
        2. Hợp đồng hủy (burn) số pool token này.
        3. Hợp đồng trả lại cho người dùng một lượng asset A và asset B tương ứng.

        Các thông số đầu vào:
            pool_xfer: Giao dịch chuyển khoản pool token.
                       Số lượng token trong giao dịch này là số lượng mà người gửi muốn đổi lại.
            pool_asset: ID của pool token, để chúng ta có thể kiểm tra số dư.
            a_asset: ID của asset A, để chúng ta có thể kiểm tra số dư và phân phối nó.
            b_asset: ID của asset B, để chúng ta có thể kiểm tra số dư và phân phối nó.
        """
        self._check_bootstrapped()

        assert pool_asset == self.pool_token, "asset pool incorrect"
        assert a_asset == self.asset_a, "asset a incorrect"
        assert b_asset == self.asset_b, "asset b incorrect"

        assert (
            pool_xfer.asset_receiver == Global.current_application_address
        ), "receiver not app address"
        assert pool_xfer.asset_amount > 0, "amount minimum not met"
        assert pool_xfer.xfer_asset == self.pool_token, "asset pool incorrect"
        assert pool_xfer.sender == Txn.sender, "sender invalid"

        # Get the total number of tokens issued
        # !important: this happens prior to receiving the current axfer of pool tokens
        pool_balance = self._current_pool_balance()
        a_amt = tokens_to_burn(
            pool_balance=pool_balance,
            supply=self._current_a_balance(),
            amount=pool_xfer.asset_amount,
        )
        b_amt = tokens_to_burn(
            pool_balance=pool_balance,
            supply=self._current_b_balance(),
            amount=pool_xfer.asset_amount,
        )

        # Send back commensurate amt of a
        do_asset_transfer(receiver=Txn.sender, asset=self.asset_a, amount=a_amt)

        # Send back commensurate amt of b
        do_asset_transfer(receiver=Txn.sender, asset=self.asset_b, amount=b_amt)
        self._update_ratio()

    @arc4.abimethod(
        default_args={
            "a_asset": "asset_a",
            "b_asset": "asset_b",
        },
    )
    def swap(
        self,
        swap_xfer: gtxn.AssetTransferTransaction,
        a_asset: Asset,
        b_asset: Asset,
    ) -> None:
        """
        Hàm này thực hiện swap (hoán đổi) một lượng asset A lấy asset B, hoặc ngược lại.

        Cách hoạt động:
        1. Người dùng gửi một lượng asset A hoặc asset B vào hợp đồng.
        2. Hợp đồng tính toán và trả lại một lượng tương ứng của asset còn lại.

        Các thông số đầu vào:
            swap_xfer: Giao dịch chuyển khoản của asset A hoặc asset B mà người dùng muốn swap.
            a_asset: ID của asset A, để chúng ta có thể:
                     - Kiểm tra số dư
                     - Chuyển khoản nếu cần (trong trường hợp người dùng swap B lấy A)
            b_asset: ID của asset B, để chúng ta có thể:
                     - Kiểm tra số dư
                     - Chuyển khoản nếu cần (trong trường hợp người dùng swap A lấy B)
        """
        self._check_bootstrapped()

        assert a_asset == self.asset_a, "asset a incorrect"
        assert b_asset == self.asset_b, "asset b incorrect"

        assert swap_xfer.asset_amount > 0, "amount minimum not met"
        assert swap_xfer.sender == Txn.sender, "sender invalid"

        match swap_xfer.xfer_asset:
            case self.asset_a:
                in_supply = self._current_b_balance()
                out_supply = self._current_a_balance()
                out_asset = self.asset_a
            case self.asset_b:
                in_supply = self._current_a_balance()
                out_supply = self._current_b_balance()
                out_asset = self.asset_b
            case _:
                assert False, "asset id incorrect"

        to_swap = tokens_to_swap(
            in_amount=swap_xfer.asset_amount, in_supply=in_supply, out_supply=out_supply
        )
        assert to_swap > 0, "send amount too low"

        do_asset_transfer(receiver=Txn.sender, asset=out_asset, amount=to_swap)
        self._update_ratio()

    @subroutine
    def _check_bootstrapped(self) -> None:
        assert self.pool_token, "bootstrap method needs to be called first"

    @subroutine
    def _update_ratio(self) -> None:
        a_balance = self._current_a_balance()
        b_balance = self._current_b_balance()

        self.ratio = a_balance * SCALE // b_balance

    @subroutine
    def _check_is_governor(self) -> None:
        assert (
            Txn.sender == self.governor
        ), "Only the account set in global_state.governor may call this method"

    @subroutine
    def _create_pool_token(self) -> Asset:
        return (
            itxn.AssetConfig(
                asset_name=b"DPT-" + self.asset_a.unit_name + b"-" + self.asset_b.unit_name,
                unit_name=b"dbt",
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
            amount=UInt64(0),
        )

    @subroutine
    def _current_pool_balance(self) -> UInt64:
        return self.pool_token.balance(Global.current_application_address)

    @subroutine
    def _current_a_balance(self) -> UInt64:
        return self.asset_a.balance(Global.current_application_address)

    @subroutine
    def _current_b_balance(self) -> UInt64:
        return self.asset_b.balance(Global.current_application_address)


##############
# Các phương thức toán học
##############
# Lưu ý quan trọng:

# 1. Vấn đề về overflow:
#    - Trong các phép tính, tùy thuộc vào đầu vào, các phương thức này có thể gây ra overflow
#      (tràn số) vượt quá giá trị tối đa của uint64.
#    - Nếu xảy ra overflow, chương trình sẽ dừng ngay lập tức.
#    - Người dùng cần hiểu rõ giới hạn của các hàm này.
#    - Nếu cần, nên thay thế bằng các phép toán byte phù hợp.

# 2. Vấn đề về chia lấy phần nguyên:
#    - Khi thực hiện phép chia, phần dư sẽ bị cắt bỏ khỏi kết quả.
#    - Cần đảm bảo rằng việc cắt bỏ phần dư này luôn có lợi cho hợp đồng.
#    - Đây là một vấn đề bảo mật tinh tế. Nếu xử lý không đúng, có thể dẫn đến
#      việc số dư của hợp đồng bị rút cạn.

@subroutine
def tokens_to_mint(
    *,
    pool_balance: UInt64,
    a_balance: UInt64,
    b_balance: UInt64,
    a_amount: UInt64,
    b_amount: UInt64,
) -> UInt64:
    """
    Tính toán số lượng pool token cần mint dựa trên số lượng asset A và B được deposit.

    Cách hoạt động:
    1. Kiểm tra xem đây có phải là lần mint đầu tiên không.
    2. Nếu là lần đầu, sử dụng công thức đặc biệt: sqrt(a_amount * b_amount) - SCALE
    3. Nếu không phải lần đầu:
       - Tính tỷ lệ deposit của cả A và B
       - Chọn tỷ lệ nhỏ hơn để đảm bảo công bằng
       - Tính số token cần mint dựa trên tỷ lệ này và số token đã phát hành

    Tham số:
    - pool_balance: Số dư hiện tại của pool token
    - a_balance, b_balance: Số dư hiện tại của asset A và B trong pool
    - a_amount, b_amount: Số lượng asset A và B được deposit

    Trả về: Số lượng pool token cần mint

    Lưu ý:
    - Sử dụng SCALE để tránh mất mát do làm tròn trong phép chia
    - TOTAL_SUPPLY là tổng số pool token có thể được phát hành
    """
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
    """
    Tính toán số lượng pool token cần burn khi người dùng rút tài sản.

    Cách hoạt động:
    1. Tính số token đã phát hành (issued)
    2. Tính tỷ lệ giữa số token muốn burn và số token đã phát hành
    3. Áp dụng tỷ lệ này vào tổng supply để xác định số token cần burn

    Tham số:
    - pool_balance: Số dư hiện tại của pool token
    - supply: Tổng supply hiện tại của pool token
    - amount: Số lượng pool token người dùng muốn burn

    Trả về: Số lượng pool token cần burn

    Lưu ý:
    - Công thức này đảm bảo tỷ lệ công bằng khi rút tài sản
    """
    issued = TOTAL_SUPPLY - pool_balance - amount
    return supply * amount // issued


@subroutine
def tokens_to_swap(*, in_amount: UInt64, in_supply: UInt64, out_supply: UInt64) -> UInt64:
    """
    Tính toán số lượng token nhận được khi thực hiện swap.

    Cách hoạt động:
    1. Tính toán tổng giá trị của token đầu vào sau khi áp dụng phí (FACTOR)
    2. Tính toán giá trị token đầu ra dựa trên công thức AMM (Automated Market Maker)
    3. Chia để có được số lượng token đầu ra

    Tham số:
    - in_amount: Số lượng token đầu vào muốn swap
    - in_supply: Số dư hiện tại của token đầu vào trong pool
    - out_supply: Số dư hiện tại của token đầu ra trong pool

    Trả về: Số lượng token đầu ra nhận được sau swap

    Lưu ý:
    - FACTOR được sử dụng để tính phí swap
    - SCALE được sử dụng để tăng độ chính xác trong phép tính

    Ví dụ:
        SCALE = 1_000
        FACTOR = 995 (phí 0.5%)
        in_amount = 100
        in_supply = 1000
        out_supply = 2000
        ---
        in_total = 1000 * (1000 - 100) + (100 * 995)
         = 1000 * 900 + 99500
         = 900000 + 99500
         = 999500
        ---
        out_total = 100 * 995 * 2000
          = 199000000
        ---
        result = out_total // in_total
               = 199000000 // 999500
               ≈ 199 (làm tròn xuống)
    """
    in_total = SCALE * (in_supply - in_amount) + (in_amount * FACTOR)
    out_total = in_amount * FACTOR * out_supply
    return out_total // in_total


@subroutine
def do_asset_transfer(*, receiver: Account, asset: Asset, amount: UInt64) -> None:
    """
    Thực hiện chuyển khoản asset trong nội bộ hợp đồng.

    Cách hoạt động:
    1. Tạo một inner transaction để chuyển asset
    2. Xác định asset cần chuyển, số lượng và người nhận
    3. Gửi transaction

    Tham số:
    - receiver: Tài khoản người nhận
    - asset: Asset cần chuyển
    - amount: Số lượng asset cần chuyển

    Lưu ý:
    - Hàm này sử dụng inner transaction, cho phép hợp đồng tự thực hiện giao dịch
    - Thường được sử dụng trong quá trình swap hoặc rút tài sản từ pool
    """
    itxn.AssetTransfer(
        xfer_asset=asset,
        asset_amount=amount,
        asset_receiver=receiver,
    ).submit()
