#pragma once

#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <map>
#include <string>
#include <variant>
#include <vector>

namespace af::json {

struct Value;
using Array = std::vector<Value>;
using Object = std::map<std::string, Value>;

struct Value {
    using Storage = std::variant<std::nullptr_t, bool, double, std::string, Array, Object>;
    Storage data{nullptr};

    bool is_null() const { return std::holds_alternative<std::nullptr_t>(data); }
    bool is_bool() const { return std::holds_alternative<bool>(data); }
    bool is_number() const { return std::holds_alternative<double>(data); }
    bool is_string() const { return std::holds_alternative<std::string>(data); }
    bool is_array() const { return std::holds_alternative<Array>(data); }
    bool is_object() const { return std::holds_alternative<Object>(data); }

    bool as_bool(bool def = false) const {
        return is_bool() ? std::get<bool>(data) : def;
    }
    double as_double(double def = 0.0) const {
        return is_number() ? std::get<double>(data) : def;
    }
    int64_t as_int64(int64_t def = 0) const {
        return is_number() ? static_cast<int64_t>(std::get<double>(data)) : def;
    }
    uint64_t as_uint64(uint64_t def = 0) const {
        return is_number() ? static_cast<uint64_t>(std::get<double>(data)) : def;
    }
    std::string as_string(const std::string& def = {}) const {
        return is_string() ? std::get<std::string>(data) : def;
    }
    const Value* get(const std::string& key) const {
        if (!is_object()) return nullptr;
        const auto& obj = std::get<Object>(data);
        auto it = obj.find(key);
        return it == obj.end() ? nullptr : &it->second;
    }
};

class Parser {
public:
    static constexpr size_t kMaxDepth = 128;
    explicit Parser(const char* text) : p_(text ? text : "") {}

    bool parse(Value& out, std::string& err) {
        depth_ = 0;
        skip_ws();
        if (!parse_value(out, err)) return false;
        skip_ws();
        if (*p_ != '\0') {
            err = "Unexpected trailing characters in JSON";
            return false;
        }
        return true;
    }

private:
    const char* p_;
    size_t depth_{0};

    void skip_ws() {
        while (*p_ && std::isspace(static_cast<unsigned char>(*p_))) ++p_;
    }

    bool parse_value(Value& out, std::string& err) {
        if (depth_ >= kMaxDepth) {
            err = "Maximum JSON nesting depth exceeded";
            return false;
        }
        struct DepthGuard {
            size_t& d;
            explicit DepthGuard(size_t& depth_ref) : d(depth_ref) { ++d; }
            ~DepthGuard() { --d; }
        } guard(depth_);

        skip_ws();
        char c = *p_;
        if (c == '\0') { err = "Unexpected end of JSON"; return false; }
        if (c == '{') return parse_object(out, err);
        if (c == '[') return parse_array(out, err);
        if (c == '"') {
            std::string s;
            if (!parse_string(s, err)) return false;
            out.data = std::move(s);
            return true;
        }
        if (c == 't' && std::strncmp(p_, "true", 4) == 0) { p_ += 4; out.data = true; return true; }
        if (c == 'f' && std::strncmp(p_, "false", 5) == 0) { p_ += 5; out.data = false; return true; }
        if (c == 'n' && std::strncmp(p_, "null", 4) == 0) { p_ += 4; out.data = nullptr; return true; }
        if (c == '-' || std::isdigit(static_cast<unsigned char>(c))) {
            char* end = nullptr;
            double v = std::strtod(p_, &end);
            if (end == p_) { err = "Invalid number"; return false; }
            p_ = end;
            out.data = v;
            return true;
        }
        err = std::string("Unexpected JSON token: ") + c;
        return false;
    }

    bool parse_string(std::string& out, std::string& err) {
        if (*p_ != '"') { err = "Expected '\"'"; return false; }
        ++p_;
        out.clear();
        while (*p_) {
            char c = *p_++;
            if (c == '"') return true;
            if (c == '\\') {
                if (!*p_) { err = "Unterminated escape"; return false; }
                char esc = *p_++;
                switch (esc) {
                    case '"': out.push_back('"'); break;
                    case '\\': out.push_back('\\'); break;
                    case '/': out.push_back('/'); break;
                    case 'b': out.push_back('\b'); break;
                    case 'f': out.push_back('\f'); break;
                    case 'n': out.push_back('\n'); break;
                    case 'r': out.push_back('\r'); break;
                    case 't': out.push_back('\t'); break;
                    case 'u': {
                        unsigned int code = 0;
                        for (int i = 0; i < 4; ++i) {
                            if (!*p_) {
                                err = "Truncated \\u escape";
                                return false;
                            }
                            char h = *p_++;
                            if (!std::isxdigit(static_cast<unsigned char>(h))) {
                                err = "Invalid \\u escape";
                                return false;
                            }
                            code <<= 4;
                            if (h >= '0' && h <= '9') code |= (h - '0');
                            else if (h >= 'a' && h <= 'f') code |= (h - 'a' + 10);
                            else code |= (h - 'A' + 10);
                        }
                        if (code < 0x80) out.push_back(static_cast<char>(code));
                        else if (code < 0x800) {
                            out.push_back(static_cast<char>(0xC0 | (code >> 6)));
                            out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
                        } else {
                            out.push_back(static_cast<char>(0xE0 | (code >> 12)));
                            out.push_back(static_cast<char>(0x80 | ((code >> 6) & 0x3F)));
                            out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
                        }
                        break;
                    }
                    default: out.push_back(esc); break;
                }
            } else {
                out.push_back(c);
            }
        }
        err = "Unterminated string";
        return false;
    }

    bool parse_array(Value& out, std::string& err) {
        ++p_;
        Array arr;
        skip_ws();
        if (*p_ == ']') { ++p_; out.data = std::move(arr); return true; }
        while (true) {
            Value elem;
            if (!parse_value(elem, err)) return false;
            arr.push_back(std::move(elem));
            skip_ws();
            if (*p_ == ']') { ++p_; out.data = std::move(arr); return true; }
            if (*p_ != ',') { err = "Expected ',' or ']'"; return false; }
            ++p_;
        }
    }

    bool parse_object(Value& out, std::string& err) {
        ++p_;
        Object obj;
        skip_ws();
        if (*p_ == '}') { ++p_; out.data = std::move(obj); return true; }
        while (true) {
            skip_ws();
            std::string key;
            if (!parse_string(key, err)) return false;
            skip_ws();
            if (*p_ != ':') { err = "Expected ':'"; return false; }
            ++p_;
            Value val;
            if (!parse_value(val, err)) return false;
            obj[std::move(key)] = std::move(val);
            skip_ws();
            if (*p_ == '}') { ++p_; out.data = std::move(obj); return true; }
            if (*p_ != ',') { err = "Expected ',' or '}'"; return false; }
            ++p_;
        }
    }
};

} // namespace af::json

namespace phonoryx {
    namespace json = af::json;
}


