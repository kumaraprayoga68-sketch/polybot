// Endpoint penerima event dari polybot. TANPA token — terima semua, balas 200.
// Sumber data dashboard sebenarnya = data/riwayat.csv di repo GitHub (public),
// jadi endpoint ini cukup meng-ACK biar bot (push_event) nggak dapet 401.
module.exports = (req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, x-polybot-token");
  if (req.method === "OPTIONS") {
    res.status(204).end();
    return;
  }
  res.status(200).json({ ok: true });
};
