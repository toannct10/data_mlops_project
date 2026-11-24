# Foody Intelligence — bản nội bộ có đầy đủ AI models

**Một ZIP chứa toàn bộ model cần cho Local mode. Không cần private model pack.**

Kèm đúng phiên bản accepted: Qwen2.5-3B GGUF, MiniLM ONNX, PhoBERT ABSA
`models/phobert_absa/absa.pt`, relevance `models/phobert_absa/relevance.joblib`,
tokenizer và cấu hình. SHA256/size nằm trong `config/portable/model_manifest.json`.
Nhóm dự án đã xác nhận quyền chia sẻ nội bộ hai fine-tuned artifacts; giấy phép
upstream/model/dataset vẫn có hiệu lực. Không coi đây là quyền công bố công khai
hoặc sử dụng thương mại. Qwen Research License chỉ cho nghiên cứu/đánh giá phi
thương mại theo điều khoản kèm theo.

## Cài và chạy Local

Cài Docker Desktop, bật WSL2/Linux amd64 containers. Giải nén một lần vào thư mục
có quyền ghi, có thể chứa dấu cách. Không cần Python trên Windows. PowerShell 5.1
hoặc 7, mở tại thư mục vừa giải nén:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\06_INSTALL_MODELS.ps1
.\01_SETUP.ps1 -Mode Local
.\02_START.ps1
.\03_HEALTH_CHECK.ps1
```

06 chỉ kiểm tra model đã kèm, không cần tham số nguồn model, không tải/thay phiên
bản tự động. Setup cũng kiểm tra tất cả hashes và dừng rõ ràng khi thiếu/sai file;
khi đó giải nén lại ZIP đã kiểm tra SHA256. Không cần lấy thêm file model AI.

Docker images và Python packages KHÔNG được xuất thành image tar trong ZIP.
Lần build đầu cần Internet tới registries, PyPI và CPU PyTorch index; versions và
public base digests đã pin. Đây là bộ model tự chứa, không phải bộ cài offline
hoàn toàn. Xem `docs/PORTABILITY_DEPENDENCY_INVENTORY.json`.

Local không cần AWS/Snowflake credentials. `.env.portable` được tạo cho máy này,
giữ mật khẩu/project/volumes khi chạy lại setup. Không chia sẻ file này. Local
dùng env-file override trống để credentials Cloud đã lưu không lọt vào container.
Không đọc `.env.local` hoặc `.env.demo` của deployment accepted.

## Giao diện và vận hành

Mặc định: BI http://localhost:8501, Ordering http://localhost:8502, API :8000,
Qwen :8193, Airflow :8088, Prometheus :9090, Grafana :3000, PostgreSQL :5433.
Nếu cổng đã được dùng, sửa PORTABLE_*_PORT trong `.env.portable`, không dừng
dịch vụ của người khác. Grafana user admin; mật khẩu trong cấu hình riêng của máy.

```powershell
.\05_DIAGNOSE.ps1
.\04_STOP.ps1
```

Project mặc định ổn định `foody-portable-demo`. Stop giữ volumes. Setup chạy lại
không phục hồi seed hoặc rebuild index. Chẩn đoán không xuất raw review/event/
credentials. Cloud là opt-in riêng, xem CLOUD_SETUP_GUIDE.md; chỉ dùng tài nguyên
non-production được cấp quyền, không dùng account/bucket/database accepted.

## Dữ liệu và giới hạn acceptance

Seed sanitized: 405412 orders / 20865 reviews / 60267 aspect rows. Source/BM25/
Chroma: 20490 documents, đã loại đúng 40 documents chứa liên hệ khỏi retrieval.
Embeddings/model versions giữ nguyên; không train hoặc re-embed lịch sử.

PhoBERT/relevance đã được load và chạy một input tổng hợp trong container offline
giới hạn 2 GB; đây không phải full benchmark. Có cảnh báo proxy được pickle với
scikit-learn 1.6.1, runtime accepted 1.7.2; smoke prediction thành công nhưng full
parity còn cần kiểm tra. Qwen/RAG full stack, máy sạch riêng, Cloud thật và CUDA
không tự động được coi là PASS. C19 evidence vẫn là hạn chế đã biết.

Đọc PORTABLE_ACCEPTANCE_REPORT.md, MODEL_CAPABILITIES.md và
CLEAN_MACHINE_ACCEPTANCE_CHECKLIST.md. Không tuyên bố fully portable trước các
kiểm tra còn SKIPPED.
