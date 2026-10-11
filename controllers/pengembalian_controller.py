from datetime import datetime, date
from models.pengembalian_model import PengembalianModel
from models.transaksi_model import TransaksiModel
from models.alat_model import AlatModel

class PengembalianController:
    @staticmethod
    def lihat_belum_kembali():
        """Daftar sewa aktif (Lunas, belum dikembalikan) + estimasi denda."""
        data = PengembalianModel.lihatBelumKembali()
        hari_ini = date.today()
        for row in data:
            rencana = row.get('tgl_kembali_rencana')
            if isinstance(rencana, datetime):
                rencana = rencana.date()
            if rencana:
                terlambat = (hari_ini - rencana).days
                terlambat = terlambat if terlambat > 0 else 0
            else:
                terlambat = 0
            row['estimasi_terlambat_hari'] = terlambat
            row['estimasi_denda'] = PengembalianModel.hitungDenda(terlambat)
        # Urutkan: yang paling lama terlambat paling atas
        data.sort(key=lambda r: r.get('estimasi_terlambat_hari', 0), reverse=True)
        return data

    @staticmethod
    def proses_kembali(id_transaksi, tanggal_kembali_aktual=None):
        """Proses pengembalian: tgl rencana diambil otomatis dari DB.

        tanggal_kembali_aktual: 'YYYY-MM-DD' atau kosong (= hari ini).
        Mengembalikan (ok, pesan).
        """
        # 1. Validasi ID
        try:
            id_transaksi = int(str(id_transaksi).strip())
        except (ValueError, AttributeError):
            return False, "ID Transaksi harus angka."

        # 2. Ambil data transaksi (untuk tgl rencana otomatis)
        trx = PengembalianModel.getTransaksi(id_transaksi)
        if not trx:
            return False, f"Transaksi ID {id_transaksi} tidak ditemukan."
        if trx['status_transaksi'] == 'Selesai':
            return False, f"Transaksi ID {id_transaksi} sudah Selesai (sudah dikembalikan)."
        if trx['status_transaksi'] != 'Lunas':
            return False, (
                f"Transaksi ID {id_transaksi} statusnya '{trx['status_transaksi']}'. "
                "Hanya status 'Lunas' yang bisa dikembalikan."
            )

        # 3. Tentukan tanggal aktual (default hari ini)
        if not tanggal_kembali_aktual or not str(tanggal_kembali_aktual).strip():
            tgl_aktual = date.today()
        else:
            try:
                tgl_aktual = datetime.strptime(str(tanggal_kembali_aktual).strip(), "%Y-%m-%d").date()
            except ValueError:
                return False, "Format tanggal salah. Gunakan YYYY-MM-DD (contoh: 2026-10-10)."

        # 4. Hitung denda dari selisih tgl rencana (DB) vs tgl aktual
        rencana = trx['tgl_kembali_rencana']
        if isinstance(rencana, datetime):
            rencana = rencana.date()
        sewa = trx['tgl_sewa']
        if isinstance(sewa, datetime):
            sewa = sewa.date()
        if sewa and tgl_aktual < sewa:
            return False, (
                f"Tanggal kembali ({tgl_aktual}) tidak boleh sebelum tanggal sewa ({sewa})."
            )
        keterlambatan_hari = (tgl_aktual - rencana).days if rencana else 0
        denda = PengembalianModel.hitungDenda(keterlambatan_hari)

        # 5. Simpan (UPDATE kondisional WHERE Lunas)
        success = PengembalianModel.prosesPengembalian(
            id_transaksi, tgl_aktual.strftime("%Y-%m-%d"), denda
        )
        if not success:
            return False, (
                f"Gagal memproses pengembalian ID {id_transaksi}: "
                "sudah diproses operator lain (tidak lagi Lunas) atau database error. "
                "Muat ulang daftar sewa aktif."
            )

        # 6. Kembalikan stok alat yang disewa (semua item alat, tanpa filter subtotal
        #    agar promo/gratis subtotal 0 pun stoknya tetap kembali).
        dipulihkan = []
        gagal_stok = []
        for d in TransaksiModel.lihatDetail(id_transaksi):
            if d.get('id_alat'):
                try:
                    qty = int(d.get('jumlah') or 0)
                except (ValueError, TypeError):
                    qty = 0
                if qty <= 0:
                    continue
                if AlatModel.ubahStok(d['id_alat'], qty):
                    dipulihkan.append(f"{d['nama_alat']} x{qty}")
                else:
                    gagal_stok.append(f"{d.get('nama_alat') or d['id_alat']} x{qty}")

        if denda > 0:
            pesan = (
                f"Pengembalian ID {id_transaksi} ({trx['nama_lengkap']}) berhasil. "
                f"Terlambat {keterlambatan_hari} hari. Total Denda: Rp{denda:,.0f} "
                "(masuk tagihan — status Tuntas setelah denda dibayar & diverifikasi)."
            )
        else:
            pesan = (
                f"Pengembalian ID {id_transaksi} ({trx['nama_lengkap']}) berhasil tepat waktu. "
                "Tidak ada denda."
            )
        if dipulihkan:
            pesan += f" Stok kembali: {', '.join(dipulihkan)}."
        if gagal_stok:
            pesan += f" [PERHATIAN] Stok gagal kembali: {', '.join(gagal_stok)} — cek manual di DB."
        return True, pesan
