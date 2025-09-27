---
layout: page
title: カテゴリー一覧
permalink: /categories/
---

## 犯罪の種類
<ul>
{% assign kinds = "ニュース, 犯罪, 性犯罪, スポーツ, 教員, 児童" | split: ", " %}
{% for cat in kinds %}
  <li>
    <a href="{{ '/categories/' | append: cat | url_encode | append: '/' | relative_url }}">{{ cat }}</a>
  </li>
{% endfor %}
</ul>

## 発生地域
<ul>
{% for c in site.categories %}
  {% assign name = c[0] %}
  {% if name contains '県' or name contains '府' or name contains '都' or name contains '道' %}
    <li>
      <a href="{{ '/categories/' | append: name | url_encode | append: '/' | relative_url }}">{{ name }}</a>
      ({{ c[1].size }})
    </li>
  {% endif %}
{% endfor %}
</ul>

## その他
<ul>
{% for c in site.categories %}
  {% assign name = c[0] %}
  {% unless name contains '県' or name contains '府' or name contains '都' or name contains '道' or kinds contains name %}
    <li>
      <a href="{{ '/categories/' | append: name | url_encode | append: '/' | relative_url }}">{{ name }}</a>
      ({{ c[1].size }})
    </li>
  {% endunless %}
{% endfor %}
</ul>
