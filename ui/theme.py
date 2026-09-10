from pathlib import Path

import streamlit as st


def inject_theme() -> None:
    css = (Path(__file__).parent / "styles.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def hero(material_count: int) -> None:
    st.markdown(
        f"""
        <section class="app-hero">
          <div class="hero-copy">
            <div class="eyebrow"><span></span> LOCAL COURSE COPILOT</div>
            <h1>智教<span>伴学</span></h1>
            <p>让每一次提问，都回到课程资料本身。</p>
          </div>
          <div class="hero-meta">
            <div class="meta-dot"></div>
            <div><strong>{material_count}</strong><small>个资料片段已就绪</small></div>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def section_intro(kicker: str, title: str, description: str) -> None:
    st.markdown(
        f"""
        <div class="section-intro">
          <div class="section-kicker">{kicker}</div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

