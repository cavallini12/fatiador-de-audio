import streamlit as st
import io
import zipfile
from datetime import datetime, timedelta, timezone
from pydub import AudioSegment

# --- Configuração da Página ---
st.set_page_config(page_title="Processador de Áudios", page_icon="✂️", layout="centered")

# --- Funções Auxiliares ---
def parse_timestamp_from_filename(filename):
    """Extrai o datetime de nomes de arquivo como '2025-10-02-07h43m27-L.ogg'."""
    try:
        base_name = filename.split('.')[0]
        if base_name.endswith('-L'):
            base_name = base_name[:-2]
        return datetime.strptime(base_name, "%Y-%m-%d-%Hh%Mm%S")
    except Exception:
        return None

def formatar_valores(com, trilha):
    return f"{int(com):04d}", f"{int(trilha):03d}"

def gerar_nome_arquivo(com_formatado, trilha_formatada, data_hora):
    data_formatada = data_hora.strftime("%Y%m%d_%H%M%S000")
    return f"{com_formatado}_{trilha_formatada}_{data_formatada}.mp3"

# --- Interface Principal ---
st.title("✂️ Processador e Fatiador de Áudios")
st.markdown("Preencha os dados abaixo, envie os arquivos `.ogg` e aguarde a geração do arquivo ZIP.")

# --- 1. Entradas do Usuário ---
st.subheader("1. Configurações")

# Configura o fuso horário de Brasília (UTC-3)
fuso_br = timezone(timedelta(hours=-3))
agora_br = datetime.now(fuso_br)

col1, col2 = st.columns(2)

with col1:
    com = st.number_input("Número COM", min_value=1, value=1234, step=1)
    trilha_inicial = st.number_input("Trilha Inicial", min_value=1, value=1, step=1)
    inicio_audio_sec = st.number_input("Tempo Início Áudio (segundos)", min_value=0, value=0, step=1)

with col2:
    # Usamos agora_br e adicionamos step=60 (passo de 60 segundos = 1 minuto)
    data_obj = st.date_input("Data Inicial", value=agora_br.date())
    hora_obj = st.time_input("Hora Inicial", value=agora_br.time(), step=60)
    limite_input = st.number_input("Máximo de Trilhas (0 = processar tudo)", min_value=0, value=0, step=1)

data_hora_atual = datetime.combine(data_obj, hora_obj)
total_trilhas_a_criar = float('inf') if limite_input == 0 else int(limite_input)

# --- 2. Upload de Arquivos ---
st.subheader("2. Arquivos de Áudio")
uploaded_files = st.file_uploader("Faça o upload de TODOS os arquivos .ogg", type=['ogg'], accept_multiple_files=True)

# --- 3. Processamento ---
if st.button("Processar Áudios", type="primary"):
    if not uploaded_files:
        st.error("⚠️ Por favor, envie pelo menos um arquivo .ogg antes de processar.")
    elif trilha_inicial == 0:
        st.error("⚠️ A trilha inicial não pode ser 0.")
    else:
        # Área de log visual
        status_text = st.empty()
        progress_bar = st.progress(0)
        
        try:
            status_text.info("Analisando arquivos e timestamps...")
            arquivos_com_timestamp = []
            
            # Cria um dicionário para acessar os arquivos em memória facilmente
            file_dict = {f.name: f for f in uploaded_files}
            
            for f in uploaded_files:
                timestamp = parse_timestamp_from_filename(f.name)
                if timestamp:
                    arquivos_com_timestamp.append((timestamp, f.name))
                else:
                    st.warning(f"Ignorando arquivo com nome mal formatado: {f.name}")

            arquivos_com_timestamp.sort(key=lambda x: x[0])

            if not arquivos_com_timestamp:
                st.error("Nenhum arquivo OGG teve um timestamp válido para ordenação.")
                st.stop()

            file_duration = timedelta(minutes=1)
            arquivos_relevantes = []
            for ts, nome in arquivos_com_timestamp:
                end_time_expected = ts + file_duration
                if end_time_expected < data_hora_atual:
                    continue
                arquivos_relevantes.append((ts, nome))

            if not arquivos_relevantes:
                st.error("Nenhum arquivo OGG cobre o horário de início solicitado.")
                st.stop()

            status_text.info("Concatenando áudios e alinhando timestamps (isso pode levar um minuto)...")
            audio_combinado = AudioSegment.empty()
            last_file_end_time_expected = None

            # --- Sincronização do Primeiro Arquivo ---
            first_timestamp, first_nome = arquivos_relevantes[0]
            segmento = AudioSegment.from_file(file_dict[first_nome], format="ogg")

            if first_timestamp < data_hora_atual:
                crop_ms = (data_hora_atual - first_timestamp).total_seconds() * 1000
                if crop_ms < len(segmento):
                    segmento = segmento[int(crop_ms):]
                    audio_combinado += segmento
                    last_file_end_time_expected = first_timestamp + file_duration
            elif first_timestamp > data_hora_atual:
                gap_ms = (first_timestamp - data_hora_atual).total_seconds() * 1000
                audio_combinado += AudioSegment.silent(duration=int(gap_ms))
                audio_combinado += segmento
                last_file_end_time_expected = first_timestamp + file_duration
            else:
                audio_combinado += segmento
                last_file_end_time_expected = first_timestamp + file_duration

            # --- Sincronização do Restante ---
            for timestamp, nome in arquivos_relevantes[1:]:
                segmento = AudioSegment.from_file(file_dict[nome], format="ogg")
                
                if last_file_end_time_expected is None:
                    continue # Segurança caso o primeiro falhe

                gap_ms = (timestamp - last_file_end_time_expected).total_seconds() * 1000

                if gap_ms > 1000:
                    audio_combinado += AudioSegment.silent(duration=int(gap_ms))
                elif gap_ms < -1000:
                    overlap_ms = abs(gap_ms)
                    if overlap_ms < len(segmento):
                        segmento = segmento[int(overlap_ms):]
                    else:
                        continue

                audio_combinado += segmento
                last_file_end_time_expected = timestamp + timedelta(minutes=1)

            if len(audio_combinado) == 0:
                st.error("Falha ao criar o áudio combinado.")
                st.stop()

            # --- Fatiamento e Criação do ZIP na Memória ---
            status_text.info("Fatiando áudios e gerando o arquivo ZIP...")
            
            duracao_segmento_ms = 245 * 1000  # 4:05
            step_segmento_ms = 240 * 1000      # 4:00
            inicio_audio_ms = inicio_audio_sec * 1000
            
            trilha_atual = trilha_inicial
            trilhas_criadas = 0
            
            # Buffer de memória para o ZIP (evita salvar no disco)
            zip_buffer = io.BytesIO()
            
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                # Primeiro segmento
                com_formatado, trilha_formatada = formatar_valores(com, trilha_atual)
                nome_arquivo = gerar_nome_arquivo(com_formatado, trilha_formatada, data_hora_atual)
                
                silencio = AudioSegment.silent(duration=inicio_audio_ms)
                fim_primeiro = duracao_segmento_ms - inicio_audio_ms
                primeiro_segmento = silencio + audio_combinado[0 : fim_primeiro]
                
                # Salva o MP3 na memória e escreve no ZIP
                mp3_buffer = io.BytesIO()
                primeiro_segmento.export(mp3_buffer, format="mp3", bitrate="192k")
                zip_file.writestr(nome_arquivo, mp3_buffer.getvalue())
                
                trilhas_criadas += 1
                progress_bar.progress(0.1) # Progresso inicial
                
                # Demais segmentos
                while trilhas_criadas < total_trilhas_a_criar:
                    trilha_atual += 1
                    data_hora_atual += timedelta(minutes=4)
                    
                    tempo_inicio_ms = (trilhas_criadas * step_segmento_ms) - inicio_audio_ms
                    tempo_inicio_ms = max(0, tempo_inicio_ms)
                    tempo_fim_ms = tempo_inicio_ms + duracao_segmento_ms
                    
                    segmento_fatiado = audio_combinado[tempo_inicio_ms : tempo_fim_ms]
                    
                    if len(segmento_fatiado) < 10000:
                        break # Fim do áudio
                    
                    com_formatado, trilha_formatada = formatar_valores(com, trilha_atual)
                    nome_arquivo = gerar_nome_arquivo(com_formatado, trilha_formatada, data_hora_atual)
                    
                    mp3_buffer = io.BytesIO()
                    segmento_fatiado.export(mp3_buffer, format="mp3", bitrate="192k")
                    zip_file.writestr(nome_arquivo, mp3_buffer.getvalue())
                    
                    trilhas_criadas += 1
                    
                    # Atualiza barra de progresso (estimativa simples se tiver limite)
                    if total_trilhas_a_criar != float('inf'):
                        prog_val = min(trilhas_criadas / total_trilhas_a_criar, 1.0)
                        progress_bar.progress(prog_val)

            progress_bar.progress(1.0)
            status_text.success(f"✅ Sucesso! {trilhas_criadas} trilha(s) gerada(s).")
            
            # --- Botão de Download ---
            com_zip = f"{int(com):04d}"
            data_zip = data_obj.strftime("%Y%m%d")
            nome_arquivo_zip = f"COM_{com_zip}_{data_zip}.zip"
            
            st.download_button(
                label="⬇️ Baixar Arquivo ZIP",
                data=zip_buffer.getvalue(),
                file_name=nome_arquivo_zip,
                mime="application/zip",
                type="primary"
            )

        except Exception as e:

            st.error(f"Ocorreu um erro durante o processamento: {e}")
