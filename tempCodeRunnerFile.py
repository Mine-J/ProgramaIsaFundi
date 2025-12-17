echa_para_post(fecha_clase: datetime) -> str:
    hora_apertura = calcular_hora_apertura(fecha_clase)
    return hora_apertura.strftime("%Y-%m-%d")