---
layout: page
title: カテゴリー一覧
permalink: /categories/
---

<h2>犯罪の種類</h2>
<ul>
{% for cat in site.categories %}
{% if cat[0] contains '犯罪' or cat[0] == '強盗' or cat[0] == '詐欺' or cat[0] == '児童' or cat[0] == '教員' %}

    <li>
      <a href="{{ site.baseurl }}/categories/{{ cat[0] }}/">{{ cat[0] }}</a>
      ({{ cat[1].size }})
    </li>
  {% endif %}
{% endfor %}
</ul>

<h2>発生地域</h2>
<ul>
{% for cat in site.categories %}
  {% if cat[0] contains '県' or cat[0] contains '府' or cat[0] contains '都' or cat[0] contains '道' %}
    <li>
      <a href="{{ site.baseurl }}/categories/{{ cat[0] }}/">{{ cat[0] }}</a>
      ({{ cat[1].size }})
    </li>
  {% endif %}
{% endfor %}
</ul>
