# 1. İstediğiniz Yapıyı Tanımlayın
from dataclasses import Field


from pydantic import BaseModel, Field


class HaberSinifi(BaseModel):
    haberTarihSaat : str = Field(description="Haber Tarih/Saat")
    haber: str = Field(description="Haber Özeti")
    etkiEdecekTarih: int = Field(description="Etki Edecek Tarih")
    onemPuanı: str = Field(description="Önem Puanı (Etki, Olasılık Çarpımı)")
    etkiYuzdesi: float = Field(description="Hissenin etkisi yüzdesi")