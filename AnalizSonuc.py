# 1. İstediğiniz Yapıyı Tanımlayın
from dataclasses import Field


from pydantic import BaseModel, Field


class AnalizSinifi(BaseModel):
    hisseAdi : str = Field(description="Hisse Adı")
    gun: str = Field(description="Gün")
    hafta_ici: str = Field(description="Hafta İçi")
    etki_yonu: str = Field(description="Etki Yönü (pozitif/negatif/nötr)")
    tahmin_yuzdesi: float = Field(description="Tahmin Yüzdesi (beklenen değişim %)")
    aciklama: str = Field(description="Açıklama: Günlük teknik analiz ve mühendislik kısıtları gerekçesi")  
