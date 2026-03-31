"""
[DEPRECATED] SNIPER AI Root Config Proxy
Este archivo ahora es un proxy hacia core/config/manager.py para mantener 
compatibilidad mientras se migra la arquitectura a módulos especializados.
"""

from core.config.manager import Config

# Mantener la exportación de Config en el espacio de nombres de la raíz
__all__ = ["Config"]
