from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
from typing import List, Optional
import io
import requests
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from PIL import Image
import os
from datetime import datetime
import uvicorn

# 初始化 FastAPI
app = FastAPI(
    title="PDF Field Filler API",
    description="填寫 PDF 表單欄位並上傳到 Supabase",
    version="1.0.0"
)

# CORS 設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 從環境變數讀取 Supabase 憑證
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# ==================== 數據模型 ====================

class FieldRect(BaseModel):
    x: float
    y: float
    width: float
    height: float

class FieldData(BaseModel):
    field_name: str
    field_type: str
    field_page_num: int
    field_rect: FieldRect
    field_answer: str

class FillPDFRequest(BaseModel):
    pdf_url: HttpUrl
    fields: List[FieldData]
    filename: str
    bucket: Optional[str] = "finishpdf"

class FillPDFResponse(BaseModel):
    success: bool
    message: str
    pdf_url: Optional[str] = None
    filename: Optional[str] = None

# ==================== Supabase HTTP API 客戶端 ====================

class SupabaseStorageClient:
    def __init__(self, url: str, key: str):
        self.url = url.rstrip('/')
        self.key = key
        
    def upload(self, bucket: str, path: str, file_data: bytes) -> dict:
        """上傳文件到 Supabase Storage"""
        upload_url = f"{self.url}/storage/v1/object/{bucket}/{path}"
        
        headers = {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/pdf"
        }
        
        response = requests.post(upload_url, data=file_data, headers=headers)
        
        if response.status_code not in [200, 201]:
            raise Exception(f"Upload failed: {response.text}")
        
        return response.json()
    
    def get_public_url(self, bucket: str, path: str) -> str:
        """獲取文件的公開 URL"""
        return f"{self.url}/storage/v1/object/public/{bucket}/{path}"

# ==================== PDF 處理類 ====================

class PDFFieldFiller:
    def __init__(self, supabase_url: str, supabase_key: str):
        self.storage = SupabaseStorageClient(supabase_url, supabase_key)
        
    def download_file(self, url: str) -> bytes:
        """下載遠端文件"""
        response = requests.get(url)
        response.raise_for_status()
        return response.content
    
    def convert_anvil_coordinates(self, anvil_y: float, page_height: float, field_height: float) -> float:
        """轉換 Anvil 座標到 PDF 座標"""
        pdf_y = page_height - anvil_y - field_height
        return pdf_y
    
    def create_overlay(self, field_data: FieldData, page_width: float, page_height: float) -> io.BytesIO:
        """創建包含填寫內容的覆蓋層 PDF"""
        packet = io.BytesIO()
        can = canvas.Canvas(packet, pagesize=(page_width, page_height))
        
        # 獲取座標
        x = field_data.field_rect.x
        anvil_y = field_data.field_rect.y
        width = field_data.field_rect.width
        height = field_data.field_rect.height
        
        # 轉換座標
        y = self.convert_anvil_coordinates(anvil_y, page_height, height)
        
        field_type = field_data.field_type
        answer = field_data.field_answer
        
        print(f"📍 處理欄位: {field_data.field_name}")
        print(f"   類型: {field_type}, Anvil y={anvil_y}, PDF y={y:.2f}")
        
        # 檢查是否為簽名欄位
        is_signature = (field_type in ['signature', 'signatureDate'] or 
                       (isinstance(answer, str) and answer.startswith('http')))
        
        if is_signature and isinstance(answer, str) and answer.startswith('http'):
            print("   🖼️  處理簽名圖片...")
            try:
                # 下載並處理簽名圖片
                img_data = self.download_file(answer)
                img = Image.open(io.BytesIO(img_data))
                
                # 臨時文件
                temp_img_path = f"temp_sig_{datetime.now().timestamp()}.png"
                img.save(temp_img_path)
                
                # 插入圖片
                padding = 2
                can.drawImage(
                    temp_img_path, 
                    x + padding, 
                    y + padding, 
                    width - 2*padding, 
                    height - 2*padding,
                    preserveAspectRatio=True,
                    mask='auto'
                )
                
                print(f"   ✅ 簽名圖片已插入")
                
                # 清理臨時文件
                if os.path.exists(temp_img_path):
                    os.remove(temp_img_path)
                    
            except Exception as e:
                print(f"   ❌ 簽名圖片處理失敗: {str(e)}")
                can.setFont("Helvetica", 10)
                can.drawString(x + 2, y + height/2, "[Signature Error]")
            
        else:
            # 處理文字欄位
            print(f"   📝 處理文字欄位: {answer}")
            font_size = min(height * 0.6, 12)
            font_size = max(font_size, 8)
            
            can.setFont("Helvetica", font_size)
            text_y = y + (height - font_size) / 2 + 2
            can.drawString(x + 3, text_y, str(answer))
            print(f"   ✅ 文字已填入")
        
        can.save()
        packet.seek(0)
        return packet
    
    def fill_pdf(self, pdf_url: str, fields_data: List[FieldData]) -> io.BytesIO:
        """在 PDF 上填寫欄位"""
        print("📥 正在下載 PDF...")
        pdf_bytes = self.download_file(pdf_url)
        pdf_reader = PdfReader(io.BytesIO(pdf_bytes))
        pdf_writer = PdfWriter()
        
        print(f"📄 PDF 共有 {len(pdf_reader.pages)} 頁")
        
        # 按頁數分組欄位
        fields_by_page = {}
        for field in fields_data:
            page_num = field.field_page_num
            if page_num not in fields_by_page:
                fields_by_page[page_num] = []
            fields_by_page[page_num].append(field)
        
        print(f"📋 欄位分佈: {dict((k, len(v)) for k, v in fields_by_page.items())}")
        
        # 處理每一頁
        for page_num in range(len(pdf_reader.pages)):
            page = pdf_reader.pages[page_num]
            
            if page_num in fields_by_page:
                print(f"\n📝 處理第 {page_num} 頁...")
                
                page_width = float(page.mediabox.width)
                page_height = float(page.mediabox.height)
                print(f"   頁面尺寸: {page_width} x {page_height}")
                
                # 創建並合併覆蓋層
                for field in fields_by_page[page_num]:
                    overlay_pdf = self.create_overlay(field, page_width, page_height)
                    overlay_reader = PdfReader(overlay_pdf)
                    overlay_page = overlay_reader.pages[0]
                    page.merge_page(overlay_page)
            
            pdf_writer.add_page(page)
        
        # 輸出
        output = io.BytesIO()
        pdf_writer.write(output)
        output.seek(0)
        return output
    
    def upload_to_supabase(self, pdf_data: io.BytesIO, filename: str, bucket: str) -> str:
        """上傳 PDF 到 Supabase Storage"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_filename = f"filled_{timestamp}_{filename}"
        
        print(f"☁️  正在上傳到 Supabase bucket '{bucket}'...")
        
        self.storage.upload(bucket, unique_filename, pdf_data.read())
        public_url = self.storage.get_public_url(bucket, unique_filename)
        
        return public_url

# ==================== API 端點 ====================

@app.get("/")
async def root():
    """健康檢查端點"""
    return {
        "service": "PDF Field Filler API",
        "status": "running",
        "version": "1.0.1"
    }

@app.get("/health")
async def health_check():
    """詳細健康檢查"""
    return {
        "status": "healthy",
        "supabase_configured": bool(SUPABASE_URL and SUPABASE_KEY),
        "timestamp": datetime.now().isoformat()
    }

@app.post("/fill-pdf", response_model=FillPDFResponse)
async def fill_pdf(request: FillPDFRequest):
    """填寫 PDF 表單欄位"""
    try:
        # 檢查 Supabase 配置
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise HTTPException(
                status_code=500, 
                detail="Supabase 未配置。請設置 SUPABASE_URL 和 SUPABASE_KEY 環境變數"
            )
        
        # 初始化處理器
        filler = PDFFieldFiller(SUPABASE_URL, SUPABASE_KEY)
        
        # 處理 PDF
        filled_pdf = filler.fill_pdf(str(request.pdf_url), request.fields)
        
        # 上傳到 Supabase
        public_url = filler.upload_to_supabase(filled_pdf, request.filename, request.bucket)
        
        print(f"✅ 完成！URL: {public_url}")
        
        return FillPDFResponse(
            success=True,
            message="PDF 填寫成功",
            pdf_url=public_url,
            filename=request.filename
        )
        
    except Exception as e:
        print(f"❌ 錯誤: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
