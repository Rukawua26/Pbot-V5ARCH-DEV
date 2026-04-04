def calculate_quant_consensus(visual_prob, context):
    """
    PROTOCOLO CIRUGÍA LÁSER: Lógica del 'Senior Quant Strategist'.
    Evalúa la cohesión entre la visión de la IA (Visual) y los datos técnicos (RSI/ADX).
    """
    if not context:
        return visual_prob, "Sin Contexto"

    # 1. Extracción de Datos
    rsi = context.get("rsi", 50)
    adx = context.get("adx", 20)
    trend = context.get("trend", "NEUTRAL")
    vol = context.get("volume", 0)
    vol_ma = context.get("volume_ma", vol)
    vol_rel = vol / vol_ma if vol_ma > 0 else 0

    # 2. Score Base (Confianza Visual del Modelo ML - 40% Peso Estructural)
    score = visual_prob * 100
    penalties = []

    # --- NUEVO: FACTOR TÉCNICO (Multiplicativo) ---
    tech_factor = 1.0

    # 3. Validación Técnica (Reglas de Cohesión - 60% Peso Técnico)

    # A. Análisis de Discrepancia RSI (Momento)
    # Excepción: Si ADX > 40 (Tendencia Parabólica), ignoramos sobrecompra/venta
    if trend == "UP" and rsi > 70 and adx < 40:
        tech_factor = 0.5
        penalties.append(("RSI Sobrecompra (Factor 0.5x)", 0))
    elif trend == "DOWN" and rsi < 30 and adx < 40:
        tech_factor = 0.5
        penalties.append(("RSI Sobreventa (Factor 0.5x)", 0))

    # B. Análisis de Fuerza ADX
    if adx < 20:
        penalties.append(("ADX Débil (<20)", 20))

    # C. Coherencia de Volumen
    if vol_rel < 0.8:
        penalties.append(("Volumen Bajo", 10))

    # 4. Cálculo de Score Real y Razón
    total_penalty = sum(item[1] for item in penalties)

    # Aplicamos penalizaciones aditivas primero, luego el factor multiplicativo
    final_score = max(0.0, score - total_penalty) * tech_factor

    if not penalties:
        reason = "✅ Consenso Técnico OK"
    else:
        details = ", ".join([f"{item[0]}" for item in penalties])
        reason = f"⚠️ Ajuste -{total_penalty}%: {details}"

    return final_score / 100.0, reason
