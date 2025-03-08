import streamlit as st
import os
import tempfile
import shutil
import openai
import re
import logging
from babelfish import Language
from subliminal import download_best_subtitles, scan_video
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Setup logging
if os.getenv("ENV") == "production":
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
else:
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")

# Load environment variables
load_dotenv()

# Import supported providers
import subliminal.providers.opensubtitles
import subliminal.providers.podnapisi
import subliminal.providers.addic7ed

# Configure Subliminal's cache
from subliminal.cache import region
try:
    region.configure('dogpile.cache.memory', expiration_time=timedelta(hours=24), replace_existing_backend=True)
    logging.debug("Subliminal cache configured successfully.")
except ValueError:
    logging.debug("Subliminal cache was already configured.")

# Supported languages
LANGUAGES = {
    'eng': 'English',
    'fas': 'Persian',
    'spa': 'Spanish',
    'fra': 'French',
    'deu': 'German',
    'zho': 'Chinese',
    'ara': 'Arabic'
}

# Environment variables
OPEN_SUBTITLES_USERNAME = os.getenv("OPEN_SUBTITLES_USERNAME")
OPEN_SUBTITLES_PASSWORD = os.getenv("OPEN_SUBTITLES_PASSWORD")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-r1-zero:free")


def search_and_download_subtitles(
    title, year, media_type, languages, providers_list,
    season=None, episode=None, provider_configs=None
):
    logging.debug(f"Starting subtitle search for title: {title}, year: {year}, media_type: {media_type}")
    temp_dir = tempfile.mkdtemp()
    dummy_path = os.path.join(temp_dir, f"{title}.mkv")
    logging.debug(f"Temporary directory created: {temp_dir}")
    
    try:
        # Create dummy file
        with open(dummy_path, 'wb') as f:
            f.write(b'dummy')
        logging.debug(f"Dummy file created at: {dummy_path}")
        
        # Scan video and set metadata
        video = scan_video(dummy_path)
        video.title = title
        video.year = year
        
        if media_type == "episode":
            video.series = title
            video.season = season
            video.episode = episode
            logging.debug(f"Set video metadata for episode: season {season}, episode {episode}")
        
        # Convert language codes to Language objects
        language_set = {Language(l) for l in languages}
        logging.debug(f"Searching subtitles for languages: {languages}")
        
        # Download subtitles using the provided configurations
        subtitles = download_best_subtitles(
            [video],
            language_set,
            providers=providers_list,
            provider_configs=provider_configs or {}
        )
        logging.debug("Subtitle download completed.")
        
        # Organize results by language code
        results = {}
        for sub in subtitles.get(video, []):
            results[sub.language.alpha3] = sub
            logging.debug(f"Subtitle found: Provider={sub.provider_name}, ID={sub.id}, Language={sub.language.alpha3}")
        
        return results
    except Exception as e:
        logging.error(f"Error in search_and_download_subtitles: {str(e)}")
        raise
    finally:
        shutil.rmtree(temp_dir)
        logging.debug(f"Temporary directory {temp_dir} removed.")


def enhance_subtitles(sub_content):
    """Enhance subtitles using OpenAI's API."""
    if not OPENROUTER_API_KEY:
        st.error("Missing OpenAI API key. Please set the OPENROUTER_API_KEY environment variable.")
        logging.error("OPENROUTER_API_KEY not found.")
        return None
    
    openai.base_url = "https://openrouter.ai/api/v1"

    openai.api_key = OPENROUTER_API_KEY

    prompt = f"""
Enhance the following subtitle text to improve naturalness, fluency, and clarity while preserving the original meaning.
IMPORTANT: Return only the SRT-formatted subtitles exactly as requested (with timing codes and numbering) and do not include any extra explanation or text.

{sub_content}

Provide the result in SRT format.
    """
    logging.debug(f"Enhancement prompt prepared: {prompt[:100]}...")  # log first 100 chars of prompt

    try:
        response = openai.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[
                {"role": "system", "content": "You are a professional subtitle translator and editor."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=3000
        )

        # enhanced_subtitles = response.choices[0].message.content.strip()
        enhanced_subtitles = response.strip()

        logging.debug("Received response from OpenAI API.")
        # Validate using a regex to check for a typical SRT block (number followed by a newline)
        # if not re.match(r'^\d+\s*\n', enhanced_subtitles):
        #     st.error("Invalid subtitle format returned by AI")
        #     logging.error("Enhanced subtitles failed validation. Format is not a valid SRT.")
        #     return None
        
        logging.debug("Enhanced subtitles validated successfully.")
        print(enhanced_subtitles)
        return enhanced_subtitles
    except Exception as e:
        st.error(f"AI Enhancement Error: {str(e)}")
        logging.error(f"Error in enhance_subtitles: {str(e)}")
        return None


def main():
    st.title("Subtitle Downloader and Enhancer")
    st.write("⚠️ Note: Requires valid provider credentials for some services.")
    
    # Initialize session state for caching search results and enhanced subtitles
    if 'search_cache' not in st.session_state:
        st.session_state.search_cache = {}
        logging.debug("Initialized session_state.search_cache.")
    if 'enhanced_subtitles' not in st.session_state:
        st.session_state.enhanced_subtitles = {}
        logging.debug("Initialized session_state.enhanced_subtitles.")
    
    # Layout for input parameters
    col1, col2 = st.columns(2)
    with col1:
        media_type = st.selectbox("Media Type", ["movie", "episode"])
        title = st.text_input("Title:")
    
    with col2:
        year = st.number_input("Year", min_value=1900, max_value=datetime.now().year, value=datetime.now().year)
        if media_type == "episode":
            season = st.number_input("Season", min_value=1, value=1)
            episode = st.number_input("Episode", min_value=1, value=1)
    
    # Language and provider selection
    selected_langs = st.multiselect(
        "Select Languages",
        options=list(LANGUAGES.keys()),
        default=['eng'],
        format_func=lambda x: LANGUAGES[x]
    )
    
    available_providers = ['opensubtitles', 'podnapisi', 'addic7ed']
    selected_providers = st.multiselect(
        "Select Providers",
        options=available_providers,
        default=['opensubtitles']
    )
    
    # Build provider configurations for Opensubtitles if selected
    provider_configs = {}
    if "opensubtitles" in selected_providers:
        provider_configs["opensubtitles"] = {
            "username": OPEN_SUBTITLES_USERNAME,
            "password": OPEN_SUBTITLES_PASSWORD
        }
        logging.debug("Provider configuration set for Opensubtitles.")
    
    # Search Subtitles button
    if st.button("Search Subtitles"):
        if not title or not selected_langs or not selected_providers:
            st.error("Please fill all required fields")
            logging.warning("Search attempted with missing fields.")
        else:
            cache_key = f"{title}_{media_type}_{year}_{'_'.join(selected_langs)}_{'_'.join(selected_providers)}"
            if media_type == "episode":
                cache_key += f"_{season}_{episode}"
            logging.debug(f"Cache key generated: {cache_key}")
            
            # Use cache if available, otherwise perform the search
            if cache_key in st.session_state.search_cache:
                results = st.session_state.search_cache[cache_key]
                logging.debug("Using cached search results.")
            else:
                try:
                    with st.spinner("Searching subtitles..."):
                        season_num = season if media_type == "episode" else None
                        episode_num = episode if media_type == "episode" else None
                        results = search_and_download_subtitles(
                            title, year, media_type, selected_langs,
                            selected_providers, season=season_num,
                            episode=episode_num, provider_configs=provider_configs
                        )
                        st.session_state.search_cache[cache_key] = results
                        logging.debug("Search results cached.")
                except Exception as e:
                    st.error(f"Error: {str(e)}")
                    logging.error(f"Error during subtitle search: {str(e)}")
                    results = {}
            
            if not results:
                st.warning("No subtitles found")
                logging.info("No subtitles were found for the search criteria.")
            else:
                st.subheader("Search Results:")
                st.markdown("""
                <style>
                .subtitle-box {
                    background-color: #f0f2f6;
                    border-radius: 10px;
                    padding: 15px;
                    margin: 10px 0;
                    border: 1px solid #ddd;
                }
                .enhancement-box {
                    background-color: #e8f5e9;
                    border-radius: 10px;
                    padding: 15px;
                    margin: 10px 0;
                    border: 1px solid #93c47d;
                }
                </style>
                """, unsafe_allow_html=True)
                
                # Iterate over selected languages and display results
                for lang in selected_langs:
                    if lang in results:
                        sub = results[lang]
                        logging.debug(f"Displaying subtitle for language: {lang}")
                        with st.container():
                            st.markdown('<div class="subtitle-box">', unsafe_allow_html=True)
                            st.markdown(f"**{LANGUAGES[lang]} Subtitle**")
                            st.write(f"- Provider: {sub.provider_name}")
                            st.write(f"- ID: {sub.id}")
                            if hasattr(sub, 'hearing_impaired'):
                                st.write(f"- Closed Captions: {sub.hearing_impaired}")
                            
                            # Download original subtitle file
                            filename = f"{title}.{lang}.srt"
                            st.download_button(
                                "Download Original",
                                data=sub.content,
                                file_name=filename,
                                mime="text/plain"
                            )
                            
                            def Button_to_enhance_subtitle_with_AI():
                                logging.info(f"Enhance button clicked for language: {lang}")
                                with st.spinner("Processing with AI..."):
                                    enhanced = enhance_subtitles(sub.content.decode('utf-8'))
                                    if enhanced:
                                        st.session_state.enhanced_subtitles[lang] = enhanced
                                        st.success("AI enhancement completed successfully!")
                                        logging.info("AI enhancement completed successfully.")
                                    else:
                                        st.error("AI enhancement failed. Please try again.")
                                        logging.error("AI enhancement failed.")
                            
                            # Button to enhance subtitle with AI
                            st.button(
                                label=f"Enhance with AI",
                                key=f"enhance_{lang}",
                                on_click=Button_to_enhance_subtitle_with_AI
                            )

                            st.markdown('</div>', unsafe_allow_html=True)
                        
                        # Display the enhanced subtitle if available
                        if lang in st.session_state.enhanced_subtitles:
                            with st.container():
                                st.markdown('<div class="enhancement-box">', unsafe_allow_html=True)
                                st.subheader(f"Enhanced {LANGUAGES[lang]} Subtitle")
                                enhanced_text = st.session_state.enhanced_subtitles[lang]
                                st.text_area("Improved Subtitles", value=enhanced_text, height=300)
                                st.download_button(
                                    "Download Enhanced",
                                    data=enhanced_text,
                                    file_name=f"{title}.{lang}_enhanced.srt",
                                    mime="text/plain"
                                )
                                if st.button("Hide Enhanced", key=f"hide_{lang}"):
                                    del st.session_state.enhanced_subtitles[lang]
                                    logging.debug(f"Enhanced subtitles hidden for language: {lang}")
                                st.markdown('</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
